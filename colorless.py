#!/usr/bin/env python

import argparse
import collections
import curses
import locale
import itertools
import os
import re
import signal
import sys
import time


class ExitSuccess(Exception):
    def __init__(self):
        self.exit_code = os.EX_OK


class ExitFailure(Exception):
    def __init__(self, exit_code, msg):
        self.exit_code = exit_code
        self.msg = msg


class TerminalDimensions:
    def __init__(self, screen):
        self.update(screen)

    def update(self, screen):
        term_dimensions = screen.getmaxyx()
        self.rows = term_dimensions[0] - 1
        self.cols = term_dimensions[1]


class SearchHistoryFile:
    def __init__(self):
        self.search_history_filepath = os.path.join(os.path.expanduser('~'), '.colorless_search_history')

    def load_search_queries(self):
        try:
            search_history_file = open(self.search_history_filepath, 'a+')
        except EnvironmentError:
            return []
        with search_history_file:
            search_history_file.seek(0)
            return [line.rstrip('\n') for line in search_history_file.readlines()]

    def write_search_queries(self, search_queries):
        try:
            search_history_file = open(self.search_history_filepath, 'w')
        except EnvironmentError:
            return
        with search_history_file:
            search_history_file.writelines(search_query + '\n' for search_query in search_queries)


class RegexCompiler:
    @staticmethod
    def compile_regex(regex, flags=0):
        try:
            return re.compile(r'({0})'.format(regex), flags)
        except re.error as exception:
            raise ExitFailure(os.EX_DATAERR, 'Compiling regex {} failed with error: "{}"'.format(regex, exception))

    @staticmethod
    def compile_smartcase_regex(regex):
        if regex.islower():
            return RegexCompiler.compile_regex(regex, re.IGNORECASE)
        return RegexCompiler.compile_regex(regex)


def sanitize_line(line):
    return line.decode(locale.getpreferredencoding(False)).replace('\x01', '\\x01').replace('\t', '    ')


class SearchHistory:
    def __init__(self, search_queries):
        self.last_search_query = None
        self.search_queries = search_queries

    def get_last_search_query(self):
        return self.last_search_query

    def get_search_queries(self):
        return self.search_queries

    def insert_search_query(self, search_query):
        self.last_search_query = search_query
        self.search_queries.insert(0, search_query)
        self.search_queries = SearchHistory._filter_duplicate_search_queries(self.search_queries)

    @staticmethod
    def _filter_duplicate_search_queries(search_queries):
        MAX_SEARCH_QUERIES = 100
        return list(collections.OrderedDict.fromkeys(search_queries))[:MAX_SEARCH_QUERIES]


class FileIterator:
    def __init__(self, input_file, term_dims):
        self.input_file = input_file
        self.line_col = 0
        self.term_dims = term_dims

    def peek_next_lines(self, count):
        position = self.input_file.tell()
        lines = [self.input_file.readline() for _ in range(count)]
        self.input_file.seek(position)
        return lines

    def peek_file_size_in_bytes(self):
        position, line_col = self.input_file.tell(), self.line_col
        self._seek_to_end_of_file()
        file_size_in_bytes = self.input_file.tell()
        self.input_file.seek(position)
        self.line_col = line_col
        return file_size_in_bytes

    def prev_line_iterator(self):
        if self.input_file.tell() == 0:
            yield ''
            return
        CHUNK_SIZE = 2048
        while True:
            lines = []
            chunk_size = min(CHUNK_SIZE, self.input_file.tell())
            self.input_file.seek(-chunk_size, os.SEEK_CUR)
            chunk = self.input_file.read(chunk_size)
            lines = chunk.splitlines(True)
            for line in reversed(lines[1:]):
                self.input_file.seek(-len(line), os.SEEK_CUR)
                yield line
            first_line_in_chunk = lines[0]
            if self.input_file.tell() == len(first_line_in_chunk):
                self.input_file.seek(-len(first_line_in_chunk), os.SEEK_CUR)
                yield first_line_in_chunk
                yield ''
                return
            elif len(lines) == 1:
                CHUNK_SIZE *= 2

    def next_line_iterator(self):
        while True:
            line = self.input_file.readline()
            if not line:
                yield ''
                return
            yield line

    def seek_to_percentage_of_file(self, percentage):
        assert 0.0 <= percentage <= 1.0
        file_size_in_bytes = self.peek_file_size_in_bytes()
        self.input_file.seek(int(percentage * file_size_in_bytes))
        next(self.prev_line_iterator())
        self.clamp_position_to_last_page()

    def tell(self):
        return self.input_file.tell()

    def seek(self, position):
        self.input_file.seek(position, os.SEEK_SET)

    def seek_to_start_of_file(self):
        self.seek(0)

    def seek_prev_wrapped_lines(self, count):
        for _ in range(count):
            self._seek_prev_wrapped_line()

    def seek_to_last_page(self):
        self._seek_to_end_of_file()
        self.seek_prev_wrapped_lines(self.term_dims.rows)

    def clamp_position_to_last_page(self):
        position = self.input_file.tell()
        line_col = self.line_col
        self.seek_to_last_page()
        if position < self.input_file.tell() or (position == self.input_file.tell() and line_col < self.line_col):
            self.input_file.seek(position)
            self.line_col = line_col

    def seek_next_wrapped_lines(self, count):
        self._seek_next_wrapped_lines(count)
        self.clamp_position_to_last_page()

    def _seek_next_wrapped_line(self):
        line = self.input_file.readline()
        sanitized_line = sanitize_line(line)
        self.line_col += self.term_dims.cols
        if self.line_col >= len(sanitized_line):
            self.line_col = 0
        else:
            self.input_file.seek(-len(line), os.SEEK_CUR)

    def _seek_next_wrapped_lines(self, count):
        for _ in range(count):
            self._seek_next_wrapped_line()

    def _seek_prev_wrapped_line(self):
        if self.line_col > 0:
            self.line_col = max(0, self.line_col - self.term_dims.cols)
            return
        line = next(self.prev_line_iterator())
        if not line:
            return
        sanitized_line = sanitize_line(line)
        if len(sanitized_line) <= self.term_dims.cols:
            return
        else:
            for line_col in reversed(range(0, len(sanitized_line), self.term_dims.cols)):
                self.line_col = line_col
                break

    def _seek_to_end_of_file(self):
        self.line_col = 0
        self.input_file.seek(0, os.SEEK_END)


class ConfigFileReader:
    def __init__(self, config_filepath):
        self.config_filepath = config_filepath

    def load_regex_to_color(self):
        if not self.config_filepath:
            return {}
        try:
            config_file = open(self.config_filepath, 'r')
        except EnvironmentError:
            raise ExitFailure(os.EX_NOINPUT, '{}: No such file or directory'.format(self.config_filepath))
        config = {}
        with config_file:
            try:
                exec(config_file.read(), config)
            except Exception as exception:
                raise ExitFailure(os.EX_NOINPUT, '{}: Load failed with error "{}"'.format(self.config_filepath, exception))
        REGEX_TO_COLOR = 'regex_to_color'
        if REGEX_TO_COLOR not in config:
            err_msg = '{}: The config must contain a dictionary named {}'.format(self.config_filepath, REGEX_TO_COLOR)
            raise ExitFailure(os.EX_NOINPUT, err_msg)
        regex_to_color = config[REGEX_TO_COLOR]
        ConfigFileReader._validate_regex_to_color(self.config_filepath, regex_to_color)
        regex_to_color = collections.OrderedDict()
        STARTING_COLOR_ID = 1
        for color_id, (regex, color) in enumerate(config[REGEX_TO_COLOR].items(), STARTING_COLOR_ID):
            regex_to_color[RegexCompiler.compile_regex(regex)] = color_id
            DEFAULT_BACKGROUND_COLOR = -1
            curses.init_pair(color_id, color, DEFAULT_BACKGROUND_COLOR)
        return regex_to_color

    @staticmethod
    def _validate_regex_to_color(config_filepath, regex_to_color):
        MAX_COLORS = 255
        if len(regex_to_color) > MAX_COLORS:
            err_msg = '{}: A maximum of {} regexes are supported but found {}'.format(config_filepath, MAX_COLORS, len(regex_to_color))
            raise ExitFailure(os.EX_NOINPUT, err_msg)
        for regex, color in regex_to_color.items():
            if color < 0 or color > 255:
                err_msg = '{}: (regex: {}, color: {}) is invalid - color must be in the range [0, {}]'.format(
                    config_filepath, regex, color, MAX_COLORS)
                raise ExitFailure(os.EX_NOINPUT, err_msg)


class ColorMaskGenerator:
    NO_COLOR = 0

    def __init__(self, regex_to_color, search_history):
        self.regex_to_color = regex_to_color
        self.search_history = search_history
        self.SEARCH_COLOR = 255
        curses.init_pair(self.SEARCH_COLOR, curses.COLOR_BLACK, curses.COLOR_YELLOW)

    def generate_color_mask(self, line):
        color_mask = [ColorMaskGenerator.NO_COLOR] * len(line)
        for compiled_regex, color in self._regex_to_color_including_last_search_query():
            tokens = compiled_regex.split(line)
            col = 0
            for index, token in enumerate(tokens):
                token_matches_regex = (index % 2 == 1)
                if token_matches_regex:
                    color_mask[col:col + len(token)] = [color] * len(token)
                col += len(token)
        return color_mask

    def _regex_to_color_including_last_search_query(self):
        regex_to_color = collections.OrderedDict(self.regex_to_color.items())
        last_search_query = self.search_history.get_last_search_query()
        if last_search_query:
            regex_to_color[RegexCompiler.compile_smartcase_regex(last_search_query)] = self.SEARCH_COLOR
        return regex_to_color.items()


class TailMode:
    def __init__(self, screen, term_dims, file_iter, screen_drawer):
        self.screen = screen
        self.term_dims = term_dims
        self.file_iter = file_iter
        self.screen_drawer = screen_drawer

    def start_tailing(self):
        try:
            file_size_in_bytes = self.file_iter.peek_file_size_in_bytes()
            while True:
                self._redraw_last_page()
                new_file_size_in_bytes = self.file_iter.peek_file_size_in_bytes()
                if file_size_in_bytes == new_file_size_in_bytes:
                    ONE_HUNDRED_MILLIS = 0.100
                    time.sleep(ONE_HUNDRED_MILLIS)
                else:
                    file_size_in_bytes = new_file_size_in_bytes
        except KeyboardInterrupt:
            pass

    def _redraw_last_page(self):
        self.file_iter.seek_to_last_page()
        self.screen_drawer.redraw_screen('Waiting for data... (interrupt to abort)')


class SearchMode:
    SEARCH_FORWARDS_CHAR = '/'
    SEARCH_BACKWARDS_CHAR = '?'

    def __init__(self, term_dims, screen, file_iter, screen_drawer, search_history, search_history_file):
        self.term_dims = term_dims
        self.screen = screen
        self.file_iter = file_iter
        self.screen_drawer = screen_drawer
        self.search_history = search_history
        self.search_history_file = search_history_file
        self.last_search_direction_char = None

    def start_new_search(self, search_direction_char):
        search_query = None
        try:
            search_query = self._wait_for_user_to_input_search_query(search_direction_char)
        except KeyboardInterrupt:
            pass
        if not search_query:
            return
        self.search_history.insert_search_query(search_query)
        self.search_history_file.write_search_queries(self.search_history.get_search_queries())
        self.last_search_direction_char = search_direction_char
        self.continue_search()

    def continue_search(self):
        self._search_with_interrupt_handling(self._continue_search)

    def continue_reverse_search(self):
        self._search_with_interrupt_handling(self._continue_reverse_search)

    def _search_with_interrupt_handling(self, search_function):
        position = self.file_iter.tell()
        try:
            search_succeeded = search_function()
            if search_succeeded:
                self.file_iter.line_col = 0
            else:
                self.file_iter.seek(position)
        except KeyboardInterrupt:
            self.file_iter.seek(position)

    def _continue_search(self):
        if self.last_search_direction_char == SearchMode.SEARCH_FORWARDS_CHAR:
            return self._search_forwards()
        elif self.last_search_direction_char == SearchMode.SEARCH_BACKWARDS_CHAR:
            return self._search_backwards()
        else:
            return False

    def _continue_reverse_search(self):
        if self.last_search_direction_char == SearchMode.SEARCH_FORWARDS_CHAR:
            return self._search_backwards()
        elif self.last_search_direction_char == SearchMode.SEARCH_BACKWARDS_CHAR:
            return self._search_forwards()
        else:
            return False

    def _search_forwards(self):
        compiled_search_query_regex = RegexCompiler.compile_smartcase_regex(self.search_history.get_last_search_query())
        next(self.file_iter.next_line_iterator())
        for line in self.file_iter.next_line_iterator():
            if not line:
                return False
            elif compiled_search_query_regex.search(sanitize_line(line)):
                next(self.file_iter.prev_line_iterator())
                self.file_iter.clamp_position_to_last_page()
                return True

    def _search_backwards(self):
        compiled_search_query_regex = RegexCompiler.compile_smartcase_regex(self.search_history.get_last_search_query())
        for line in self.file_iter.prev_line_iterator():
            if not line:
                return False
            elif compiled_search_query_regex.search(sanitize_line(line)):
                return True

    def _wait_for_user_to_input_search_query(self, search_direction_char):
        search_prefix = ''
        search_suffix = ''
        search_queries = self.search_history.get_search_queries()
        search_history_index = -1
        KEY_DELETE = 127
        self.screen_drawer.redraw_screen(search_direction_char)
        while True:
            user_input = self.screen.getch()
            if user_input == ord('\n'):
                break
            elif user_input == KEY_DELETE or user_input == curses.KEY_BACKSPACE:
                search_prefix = search_prefix[:-1]
            elif 0 <= user_input <= 255:
                search_prefix += chr(user_input)
            elif user_input == curses.KEY_LEFT and len(search_prefix) > 0:
                search_prefix, search_suffix = search_prefix[:-1], search_prefix[-1] + search_suffix
            elif user_input == curses.KEY_RIGHT and len(search_suffix) > 0:
                search_prefix, search_suffix = search_prefix + search_suffix[0], search_suffix[1:]
            elif user_input == curses.KEY_UP and search_history_index < len(search_queries) - 1:
                search_history_index += 1
                search_prefix, search_suffix = search_queries[search_history_index], ''
            elif user_input == curses.KEY_DOWN and search_history_index > 0:
                search_history_index -= 1
                search_prefix, search_suffix = search_queries[search_history_index], ''
            self.screen_drawer.redraw_screen(search_direction_char)
            self.screen.move(self.term_dims.rows, 1)
            self.screen.clrtoeol()
            search_query = search_prefix + search_suffix
            visible_search_query = search_query[:self.term_dims.cols - 2]
            self.screen.addstr(self.term_dims.rows, 1, visible_search_query)
            self.screen.move(self.term_dims.rows, min(len(search_prefix), self.term_dims.cols - 2) + 1)
            self.screen.refresh()
        return search_prefix + search_suffix


class ScreenDrawer:
    def __init__(self, screen, term_dims, color_mask_generator, file_iter):
        self.screen = screen
        self.term_dims = term_dims
        self.color_mask_generator = color_mask_generator
        self.file_iter = file_iter

    def redraw_screen(self, prompt):
        self.term_dims.update(self.screen)
        self.screen.move(0, 0)
        row = 0
        self.screen.erase()
        for i, line in enumerate(self.file_iter.peek_next_lines(self.term_dims.rows)):
            if not line or row == self.term_dims.rows:
                break
            line = sanitize_line(line)
            if i == 0:
                line = line[self.file_iter.line_col:]
            color_mask = self.color_mask_generator.generate_color_mask(line)
            wrapped_lines = self._wrap(line, self.term_dims.cols)
            wrapped_color_masks = self._wrap(color_mask, self.term_dims.cols)
            for (wrapped_line, wrapped_color_mask) in zip(wrapped_lines, wrapped_color_masks):
                if row == self.term_dims.rows:
                    break
                self.screen.addstr(row, 0, wrapped_line)
                self._draw_colored_line(row, wrapped_line, wrapped_color_mask)
                row += 1
        self.screen.addstr(self.term_dims.rows, 0, prompt[:self.term_dims.cols - 2])
        self.screen.refresh()

    def _wrap(self, line, cols):
        return [line[i:i + cols] for i in range(0, len(line), cols)]

    def _draw_colored_line(self, row, wrapped_line, wrapped_color_mask):
        col = 0
        for color, length in self._distinct_colors(wrapped_color_mask):
            if color != 0:
                self.screen.addstr(row, col, wrapped_line[col:col + length], curses.color_pair(color))
            col += length

    def _distinct_colors(self, wrapped_color_mask):
        return [(color, len(list(group_iter))) for color, group_iter in itertools.groupby(wrapped_color_mask)]


def run_curses(screen, input_file, config_filepath):
    curses.use_default_colors()
    VERY_VISIBLE = 2
    curses.curs_set(VERY_VISIBLE)
    search_history_file = SearchHistoryFile()
    search_queries = search_history_file.load_search_queries()
    search_history = SearchHistory(search_queries)
    config_file_reader = ConfigFileReader(config_filepath)
    regex_to_color = config_file_reader.load_regex_to_color()
    color_mask_generator = ColorMaskGenerator(regex_to_color, search_history)
    term_dims = TerminalDimensions(screen)
    file_iter = FileIterator(input_file, term_dims)
    screen_drawer = ScreenDrawer(screen, term_dims, color_mask_generator, file_iter)
    search_mode = SearchMode(term_dims, screen, file_iter, screen_drawer, search_history, search_history_file)
    tail_mode = TailMode(screen, term_dims, file_iter, screen_drawer)
    while True:
        try:
            screen_drawer.redraw_screen(':')
            user_input = screen.getch()
            if user_input == ord('q'):
                return os.EX_OK
            elif user_input == ord('j'):
                file_iter.seek_next_wrapped_lines(1)
            elif user_input == ord('k'):
                file_iter.seek_prev_wrapped_lines(1)
            elif user_input == ord('d'):
                file_iter.seek_next_wrapped_lines(int(term_dims.rows / 2))
            elif user_input == ord('u'):
                file_iter.seek_prev_wrapped_lines(int(term_dims.rows / 2))
            elif user_input == ord('f'):
                file_iter.seek_next_wrapped_lines(term_dims.rows)
            elif user_input == ord('b'):
                file_iter.seek_prev_wrapped_lines(term_dims.rows)
            elif user_input == ord('g'):
                file_iter.seek_to_start_of_file()
            elif user_input == ord('G'):
                file_iter.seek_to_last_page()
            elif user_input == ord('H'):
                file_iter.seek_to_percentage_of_file(0.25)
            elif user_input == ord('M'):
                file_iter.seek_to_percentage_of_file(0.50)
            elif user_input == ord('L'):
                file_iter.seek_to_percentage_of_file(0.75)
            elif user_input == ord('F'):
                tail_mode.start_tailing()
            elif user_input == ord(SearchMode.SEARCH_FORWARDS_CHAR):
                search_mode.start_new_search(SearchMode.SEARCH_FORWARDS_CHAR)
            elif user_input == ord(SearchMode.SEARCH_BACKWARDS_CHAR):
                search_mode.start_new_search(SearchMode.SEARCH_BACKWARDS_CHAR)
            elif user_input == ord('n'):
                search_mode.continue_search()
            elif user_input == ord('N'):
                search_mode.continue_reverse_search()
        except KeyboardInterrupt:
            pass


def run(args):
    description = 'A less-like pager utility with regex highlighting capabilities'
    epilog = '\n'.join(['Available commands:',
                        'j: move down one line',
                        'k: move up one line',
                        'd: move down half a page',
                        'u: move up half a page',
                        'f: move down a page',
                        'b: move up a page',
                        'g: go to beginning of file',
                        'G: go to end of file',
                        'H: go to 25% of file',
                        'M: go to 50% of file',
                        'L: go to 75% of file',
                        'F: tail file',
                        '/: search forwards',
                        '?: search backwards',
                        'n: continue search',
                        'N: reverse search',
                        'q: quit'])
    arg_parser = argparse.ArgumentParser(description=description, epilog=epilog, formatter_class=argparse.RawTextHelpFormatter)
    arg_parser.add_argument('-c', '--config-filepath', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    if len(args) == 0:
        arg_parser.print_help()
        return os.EX_USAGE
    args = arg_parser.parse_args()
    try:
        input_file = open(args.filepath, 'rb')
    except EnvironmentError:
        raise ExitFailure(os.EX_NOINPUT, '{}: No such file or directory'.format(args.filepath))
    with input_file:
        return curses.wrapper(run_curses, input_file, args.config_filepath)


def main():
    def sigterm_handler(signal, frame):
        raise ExitSuccess()
    signal.signal(signal.SIGTERM, sigterm_handler)
    locale.setlocale(locale.LC_ALL, '')
    try:
        exit_code = run(sys.argv[1:])
    except ExitSuccess as e:
        exit_code = e.exit_code
    except ExitFailure as e:
        sys.stderr.write(e.msg + '\n')
        exit_code = e.exit_code
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
