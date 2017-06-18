#!/usr/bin/env python

import argparse
import collections
import curses
import itertools
import os
import re
import signal
import sys
import time


class TerminalDimensions:
    def __init__(self, screen):
        self.update(screen)

    def update(self, screen):
        term_dimensions = screen.getmaxyx()
        self.rows = term_dimensions[0] - 1
        self.cols = term_dimensions[1]


def get_search_history_filepath():
    return os.path.join(os.path.expanduser('~'), '.colorless_search_history')


def load_search_queries_from_search_history_file():
    try:
        search_history_file = open(get_search_history_filepath(), 'a+')
    except EnvironmentError:
        return []
    else:
        with search_history_file:
            search_history_file.seek(0)
            return [line.rstrip('\n') for line in search_history_file.readlines()]


def write_search_queries_to_search_history_file(search_queries):
    try:
        search_history_file = open(get_search_history_filepath(), 'w')
    except EnvironmentError:
        pass
    else:
        with search_history_file:
            search_history_file.writelines(search_query + '\n' for search_query in search_queries)


def to_smartcase_regex(text):
    if text.islower():
        return re.compile(r'({0})'.format(text), re.IGNORECASE)
    return re.compile(r'({0})'.format(text))


class SearchHistory:
    def __init__(self, search_queries):
        UNMATCHABLE_REGEX = re.compile('a^')
        self.last_search_query_as_regex = UNMATCHABLE_REGEX
        self.search_queries = search_queries

    def get_last_search_query_as_regex(self):
        return self.last_search_query_as_regex

    def get_search_queries(self):
        return self.search_queries

    def insert_search_query(self, search_query):
        self.last_search_query_as_regex = to_smartcase_regex(search_query)
        self.search_queries.insert(0, search_query)
        self._filter_duplicate_search_queries()

    def _filter_duplicate_search_queries(self):
        MAX_SEARCH_QUERIES = 100
        self.search_queries = list(collections.OrderedDict.fromkeys(self.search_queries))[:MAX_SEARCH_QUERIES]


class FileIterator:
    def __init__(self, input_file, term_dims):
        self.input_file = input_file
        self.term_dims = term_dims

    def peek_next_lines(self, count):
        position = self.input_file.tell()
        lines = [self._read_next_line() for _ in range(count)]
        self.input_file.seek(position)
        return lines

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

    def get_file_size_in_bytes(self):
        position = self.input_file.tell()
        self._seek_to_end_of_file()
        file_size_in_bytes = self.input_file.tell()
        self.input_file.seek(position)
        return file_size_in_bytes

    def seek_to_percentage_of_file(self, percentage):
        assert 0.0 <= percentage <= 1.0
        file_size_in_bytes = self.get_file_size_in_bytes()
        self.input_file.seek(percentage * file_size_in_bytes)
        next(self.prev_line_iterator())
        self.clamp_position_to_last_page()

    def seek_to_start_of_file(self):
        self.input_file.seek(0, os.SEEK_SET)

    def seek_prev_wrapped_line(self):
        line = next(self.prev_line_iterator())
        wrapped_lines = wrap(line, self.term_dims.cols)
        for wrapped_line in wrapped_lines[:-1]:
            self.input_file.seek(len(wrapped_line), os.SEEK_CUR)

    def seek_prev_wrapped_lines(self, count):
        for i in range(count):
            self.seek_prev_wrapped_line()

    def seek_to_last_page(self):
        self._seek_to_end_of_file()
        self.seek_prev_wrapped_lines(self.term_dims.rows)

    def clamp_position_to_last_page(self):
        position = self.input_file.tell()
        self.seek_to_last_page()
        self.input_file.seek(min(position, self.input_file.tell()))

    def seek_next_wrapped_lines(self, count):
        self._seek_next_wrapped_lines(count)
        self.clamp_position_to_last_page()

    def search_forwards(self, search_regex):
        position = self.input_file.tell()
        try:
            self._search_forwards(search_regex)
        except KeyboardInterrupt:
            self.input_file.seek(position)

    def search_backwards(self, search_regex):
        position = self.input_file.tell()
        try:
            self._search_backwards(search_regex)
        except KeyboardInterrupt:
            self.input_file.seek(position)

    def _search_forwards(self, search_regex):
        position = self.input_file.tell()
        line = self._read_next_line()
        while True:
            line = self._read_next_line()
            if not line:
                self.input_file.seek(position)
                return
            elif search_regex.search(line):
                next(self.prev_line_iterator())
                self.clamp_position_to_last_page()
                return

    def _search_backwards(self, search_regex):
        position = self.input_file.tell()
        for line in self.prev_line_iterator():
            if not line:
                self.input_file.seek(position)
                return
            elif search_regex.search(line):
                return

    def _read_next_line(self):
        return self.input_file.readline()

    def _seek_next_wrapped_line(self):
        line = self._read_next_line()
        if len(line) > self.term_dims.cols:
            self.input_file.seek(self.term_dims.cols - len(line), os.SEEK_CUR)

    def _seek_next_wrapped_lines(self, count):
        for i in range(count):
            self._seek_next_wrapped_line()

    def _seek_to_end_of_file(self):
        self.input_file.seek(0, os.SEEK_END)


class RegexToColor:
    def __init__(self, config_filepath, search_history):
        self.regex_to_color = collections.OrderedDict()
        self.search_history = search_history
        self.SEARCH_COLOR = 255
        curses.init_pair(self.SEARCH_COLOR, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        if config_filepath:
            self._load_config(config_filepath)

    def to_colored_line(self, line):
        colored_line = [0] * len(line)
        for regex, color in self._items():
            tokens = regex.split(line)
            col = 0
            for index, token in enumerate(tokens):
                token_matches_regex = (index % 2 == 1)
                if token_matches_regex:
                    colored_line[col:col + len(token)] = [color] * len(token)
                col += len(token)
        return colored_line

    def _load_config(self, config_filepath):
        config = {}
        execfile(config_filepath, config)
        assert 'regex_to_color' in config, 'Config file is invalid. It must contain a dictionary named regex_to_color of {str: int}.'
        for (regex, color) in config['regex_to_color'].items():
            assert 1 <= color <= 254, '\'{0}\': {1} is invalid. Color must be in the range [1, 254].'.format(regex, color)
            self.regex_to_color[re.compile(r'({0})'.format(regex))] = color
            DEFAULT_BACKGROUND_COLOR = -1
            curses.init_pair(color, color, DEFAULT_BACKGROUND_COLOR)

    def _items(self):
        regex_to_color = collections.OrderedDict(self.regex_to_color.items())
        regex_to_color[self.search_history.get_last_search_query_as_regex()] = self.SEARCH_COLOR
        return regex_to_color.items()


class TailMode:
    def __init__(self, screen, term_dims, file_iter, regex_to_color):
        self.screen = screen
        self.term_dims = term_dims
        self.file_iter = file_iter
        self.regex_to_color = regex_to_color

    def run(self):
        try:
            self.screen.nodelay(1)
            curses.curs_set(0)
            while True:
                file_size_in_bytes = self.file_iter.get_file_size_in_bytes()
                self._redraw_last_page()
                while file_size_in_bytes == self.file_iter.get_file_size_in_bytes():
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        self.screen.erase()
        self.screen.nodelay(0)
        curses.curs_set(1)
        self.term_dims.update(self.screen)
        self.file_iter.seek_to_last_page()

    def _redraw_last_page(self):
        if self.screen.getch() == curses.KEY_RESIZE:
            self.term_dims.update(screen)
        self.file_iter.seek_to_last_page()
        redraw_screen(self.screen, self.term_dims, self.regex_to_color, self.file_iter,
                      'Waiting for data... (interrupt to abort)'[:self.term_dims.cols - 2])


class SearchMode:
    def __init__(self, screen, term_dims, file_iter, search_history):
        self.screen = screen
        self.term_dims = term_dims
        self.file_iter = file_iter
        self.search_history = search_history
        self.continue_search = lambda: None
        self.continue_reverse_search = lambda: None

    def run(self, input_key):
        try:
            self.screen.addstr(self.term_dims.rows, 0, input_key)
            curses.echo()
            search_query = self._wait_for_user_to_input_search_query()
        except KeyboardInterrupt:
            return
        finally:
            curses.noecho()
            self.screen.erase()
        if not search_query:
            return
        self.search_history.insert_search_query(search_query)
        write_search_queries_to_search_history_file(self.search_history.get_search_queries())
        search_regex = self.search_history.get_last_search_query_as_regex()
        if input_key == '/':
            self.continue_search = lambda: self.file_iter.search_forwards(search_regex)
            self.continue_reverse_search = lambda: self.file_iter.search_backwards(search_regex)
        else:
            self.continue_search = lambda: self.file_iter.search_backwards(search_regex)
            self.continue_reverse_search = lambda: self.file_iter.search_forwards(search_regex)
        self.continue_search()

    def _wait_for_user_to_input_search_query(self):
        search_prefix = ''
        search_suffix = ''
        search_queries = self.search_history.get_search_queries()
        search_history_index = -1
        KEY_DELETE = 127
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
            self.screen.move(self.term_dims.rows, 1)
            self.screen.clrtoeol()
            self.screen.addstr(self.term_dims.rows, 1, search_prefix + search_suffix)
            self.screen.move(self.term_dims.rows, len(search_prefix) + 1)
            self.screen.refresh()
        return search_prefix + search_suffix


def wrap(line, cols):
    return [line[i:i + cols] for i in range(0, len(line), cols)]


def distinct_colors(wrapped_colored_line):
    return [(color, len(list(group_iter))) for color, group_iter in itertools.groupby(wrapped_colored_line)]


def draw_colored_line(screen, row, wrapped_line, wrapped_colored_line):
    col = 0
    for color, length in distinct_colors(wrapped_colored_line):
        if color != 0:
            screen.addstr(row, col, wrapped_line[col:col + length], curses.color_pair(color))
        col += length


def redraw_screen(screen, term_dims, regex_to_color, file_iter, prompt):
    screen.move(0, 0)
    row = 0
    for line in file_iter.peek_next_lines(term_dims.rows):
        if not line or row == term_dims.rows:
            break
        colored_line = regex_to_color.to_colored_line(line)
        wrapped_lines = wrap(line, term_dims.cols)
        wrapped_colored_lines = wrap(colored_line, term_dims.cols)
        for (wrapped_line, wrapped_colored_line) in zip(wrapped_lines, wrapped_colored_lines):
            if row == term_dims.rows:
                break
            screen.addstr(row, 0, wrapped_line)
            draw_colored_line(screen, row, wrapped_line, wrapped_colored_line)
            row += 1
    screen.addstr(term_dims.rows, 0, prompt)
    screen.refresh()


def run_curses(screen, input_file, config_filepath):
    curses.use_default_colors()
    VERY_VISIBLE = 2
    curses.curs_set(VERY_VISIBLE)
    search_queries = load_search_queries_from_search_history_file()
    search_history = SearchHistory(search_queries)
    regex_to_color = RegexToColor(config_filepath, search_history)
    term_dims = TerminalDimensions(screen)
    file_iter = FileIterator(input_file, term_dims)
    search_mode = SearchMode(screen, term_dims, file_iter, search_history)
    tail_mode = TailMode(screen, term_dims, file_iter, regex_to_color)
    while True:
        try:
            redraw_screen(screen, term_dims, regex_to_color, file_iter, ':')
            user_input = screen.getch()
            if user_input == ord('q'):
                return os.EX_OK
            elif user_input == curses.KEY_RESIZE:
                term_dims.update(screen)
            elif user_input == ord('j'):
                file_iter.seek_next_wrapped_lines(1)
            elif user_input == ord('k'):
                file_iter.seek_prev_wrapped_lines(1)
            elif user_input == ord('d'):
                file_iter.seek_next_wrapped_lines(term_dims.rows / 2)
            elif user_input == ord('u'):
                file_iter.seek_prev_wrapped_lines(term_dims.rows / 2)
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
                tail_mode.run()
            elif user_input == ord('/'):
                search_mode.run('/')
            elif user_input == ord('?'):
                search_mode.run('?')
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
        input_file = open(args.filepath, 'r')
    except EnvironmentError:
        sys.stderr.write('{}: No such file or directory'.format(args.filepath))
        return os.EX_NOINPUT
    else:
        with input_file:
            return curses.wrapper(run_curses, input_file, args.config_filepath)


def main():
    signal.signal(signal.SIGTERM, lambda signal, frame: sys.exit(os.EX_OK))
    exit_code = run(sys.argv[1:])
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
