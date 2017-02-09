#!/usr/bin/env python

import argparse
import collections
import curses
import itertools
import os
import re
import sys
import time

class TerminalDimensions:
    def __init__(self, screen):
        self.update(screen)

    def update(self, screen):
        term_dimensions = screen.getmaxyx()
        self.rows = term_dimensions[0] - 1
        self.cols = term_dimensions[1]

class SearchHistoryIterator:
    def __init__(self, queries):
        self.index = 0
        self.queries = queries

    def next(self):
        return self.__iter_by(1)

    def prev(self):
        return self.__iter_by(-1)

    def __iter_by(self, count):
        self.index = max(0, min(self.index + count, len(self.queries) - 1))
        return self.queries[self.index]

class SearchHistory:
    def __init__(self):
        self.last_query = None
        self.filepath = os.path.join(os.path.expanduser('~'), '.colorless_search_history')
        self.__load_search_history_from_file()

    def add(self, query):
        self.last_query = query
        self.queries.insert(0, query)
        MAX_QUERIES = 50
        self.queries = list(collections.OrderedDict.fromkeys(self.queries))[:MAX_QUERIES]
        self.__write_search_history_to_file()

    def get_last_query_as_regex(self):
        assert self.last_query
        return self.__to_smartcase_regex(self.last_query)

    def get_last_query(self):
        return self.last_query

    def to_iterator(self):
        return SearchHistoryIterator([''] + self.queries)

    def __write_search_history_to_file(self):
        with open(self.filepath, 'w') as search_history_file:
            search_history_file.writelines(s + '\n' for s in self.queries)

    def __load_search_history_from_file(self):
        with open(self.filepath, 'a+') as search_history_file:
            search_history_file.seek(0)
            self.queries = [line.rstrip('\n') for line in search_history_file.readlines()]

    def __to_smartcase_regex(self, search_query):
        if search_query.islower():
            return re.compile(r'({0})'.format(search_query), re.IGNORECASE)
        return re.compile(r'({0})'.format(search_query))

class FileIterator:
    def __init__(self, input_file, term_dims):
        self.input_file = input_file
        self.term_dims = term_dims

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
            if self.input_file.tell() == len(lines[0]):
                self.input_file.seek(-len(lines[0]), os.SEEK_CUR)
                yield lines[0]
                yield ''
                return
            elif len(lines) == 1:
                CHUNK_SIZE *= 2

    def retrieve_next_lines(self, count):
        position = self.input_file.tell()
        lines = [self.next_line() for _ in range(count)]
        self.input_file.seek(position)
        return lines

    def next_line(self):
        return self.input_file.readline()

    def get_file_size_in_bytes(self):
        position = self.input_file.tell()
        self.__seek_to_end_of_file()
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
        self.__seek_to_end_of_file()
        self.seek_prev_wrapped_lines(self.term_dims.rows)

    def clamp_position_to_last_page(self):
        position = self.input_file.tell()
        self.seek_to_last_page()
        self.input_file.seek(min(position, input_file.tell()))

    def seek_next_wrapped_lines_and_clamp_position(self, count):
        self.__seek_next_wrapped_lines(count)
        self.clamp_position_to_last_page()

    def search_forwards(self, search_regex):
        position = self.input_file.tell()
        try:
            self.__search_forwards(search_regex)
        except KeyboardInterrupt:
            self.input_file.seek(position)

    def search_backwards(self, search_regex):
        position = self.input_file.tell()
        try:
            self.__search_backwards(search_regex)
        except KeyboardInterrupt:
            self.input_file.seek(position)

    def __search_forwards(self, search_regex):
        position = self.input_file.tell()
        line = self.next_line()
        while True:
            line = self.next_line()
            if not line:
                self.input_file.seek(position)
                return
            elif search_regex.search(line):
                next(self.prev_line_iterator())
                self.clamp_position_to_last_page()
                return

    def __search_backwards(self, search_regex):
        position = self.input_file.tell()
        for line in self.prev_line_iterator():
            if not line:
                self.input_file.seek(position)
                return
            elif search_regex.search(line):
                return

    def __seek_next_wrapped_line(self):
        line = self.next_line()
        if len(line) > self.term_dims.cols:
            self.input_file.seek(self.term_dims.cols - len(line), os.SEEK_CUR)

    def __seek_next_wrapped_lines(self, count):
        for i in range(count):
            self.__seek_next_wrapped_line()

    def __seek_to_end_of_file(self):
        self.input_file.seek(0, os.SEEK_END)

class RegexToColor:
    def __init__(self, config_filepath, search_history):
        self.regex_to_color = collections.OrderedDict()
        self.search_history = search_history
        self.SEARCH_COLOR = 255
        curses.init_pair(self.SEARCH_COLOR, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        if config_filepath:
            self.__load_config(config_filepath)

    def to_colored_line(self, line):
        colored_line = [0] * len(line)
        for regex, color in self.__items():
            tokens = regex.split(line)
            col = 0
            for index, token in enumerate(tokens):
                token_matches_regex = (index % 2 == 1)
                if token_matches_regex:
                    colored_line[col:col + len(token)] = [color] * len(token)
                col += len(token)
        return colored_line

    def __load_config(self, config_filepath):
        config = {}
        execfile(config_filepath, config)
        assert 'regex_to_color' in config, 'Config file is invalid. It must contain a dictionary named regex_to_color of {str: int}.'
        for (regex, color) in config['regex_to_color'].items():
            assert 1 <= color <= 254, '\'{0}\': {1} is invalid. Color must be in the range [1, 254].'.format(regex, color)
            self.regex_to_color[re.compile(r'({0})'.format(regex))] = color
            DEFAULT_BACKGROUND_COLOR = -1
            curses.init_pair(color, color, DEFAULT_BACKGROUND_COLOR)

    def __items(self):
        regex_to_color = collections.OrderedDict(self.regex_to_color.items())
        if self.search_history.get_last_query():
            regex_to_color[self.search_history.get_last_query_as_regex()] = self.SEARCH_COLOR
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
                self.__redraw_last_page()
                while file_size_in_bytes == self.file_iter.get_file_size_in_bytes():
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        self.screen.erase()
        self.screen.nodelay(0)
        curses.curs_set(1)
        self.term_dims.update(self.screen)
        self.file_iter.seek_to_last_page()

    def __redraw_last_page(self):
        if self.screen.getch() == curses.KEY_RESIZE:
            self.term_dims.update(screen)
        self.file_iter.seek_to_last_page()
        redraw_screen(self.screen, self.term_dims, self.regex_to_color, self.file_iter, 'Waiting for data... (interrupt to abort)'[:self.term_dims.cols - 2])

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
            search_query = self.__wait_for_user_input()
        except KeyboardInterrupt:
            return
        finally:
            curses.noecho()
            self.screen.erase()
        if not search_query:
            return
        self.search_history.add(search_query)
        search_regex = self.search_history.get_last_query_as_regex()
        if input_key == '/':
            self.continue_search = lambda: self.file_iter.search_forwards(search_regex)
            self.continue_reverse_search = lambda: self.file_iter.search_backwards(search_regex)
        else:
            self.continue_search = lambda: self.file_iter.search_backwards(search_regex)
            self.continue_reverse_search = lambda: self.file_iter.search_forwards(search_regex)
        self.continue_search()

    def __wait_for_user_input(self):
        search_query = ''
        search_history_iter = self.search_history.to_iterator()
        KEY_DELETE = 127
        input_to_search_query = {
            KEY_DELETE : lambda: search_query[:-1],
            curses.KEY_BACKSPACE : lambda: search_query[:-1],
            curses.KEY_UP : lambda: search_history_iter.next(),
            curses.KEY_DOWN : lambda: search_history_iter.prev()
        }

        while True:
            user_input = self.screen.getch()
            if user_input in input_to_search_query:
                search_query = input_to_search_query[user_input]()
            elif 0 <= user_input <= 255:
                if chr(user_input) == '\n':
                    break
                search_query += chr(user_input)
            self.screen.move(self.term_dims.rows, 1)
            self.screen.clrtoeol()
            self.screen.addstr(self.term_dims.rows, 1, search_query)
            self.screen.refresh()
        return search_query

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
    for line in file_iter.retrieve_next_lines(term_dims.rows):
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

def main(screen, input_file, config_filepath):
    curses.use_default_colors()
    search_history = SearchHistory()
    regex_to_color = RegexToColor(config_filepath, search_history)
    term_dims = TerminalDimensions(screen)
    file_iter = FileIterator(input_file, term_dims)
    search_mode = SearchMode(screen, term_dims, file_iter, search_history)
    tail_mode = TailMode(screen, term_dims, file_iter, regex_to_color)
    input_to_action = {ord(key): action for (key, action) in {
        'j' : lambda: file_iter.seek_next_wrapped_lines_and_clamp_position(1),
        'k' : lambda: file_iter.seek_prev_wrapped_lines(1),
        'd' : lambda: file_iter.seek_next_wrapped_lines_and_clamp_position(term_dims.rows / 2),
        'u' : lambda: file_iter.seek_prev_wrapped_lines(term_dims.rows / 2),
        'f' : lambda: file_iter.seek_next_wrapped_lines_and_clamp_position(term_dims.rows),
        'b' : lambda: file_iter.seek_prev_wrapped_lines(term_dims.rows),
        'g' : lambda: file_iter.seek_to_start_of_file(),
        'G' : lambda: file_iter.seek_to_last_page(),
        'H' : lambda: file_iter.seek_to_percentage_of_file(0.25),
        'M' : lambda: file_iter.seek_to_percentage_of_file(0.50),
        'L' : lambda: file_iter.seek_to_percentage_of_file(0.75),
        'F' : lambda: tail_mode.run(),
        '/' : lambda: search_mode.run('/'),
        '?' : lambda: search_mode.run('?'),
        'n' : lambda: search_mode.continue_search(),
        'N' : lambda: search_mode.continue_reverse_search(),
        'q' : lambda: sys.exit(os.EX_OK)
    }.items()}

    while True:
        redraw_screen(screen, term_dims, regex_to_color, file_iter, ':')
        try:
            user_input = screen.getch()
        except KeyboardInterrupt:
            continue
        if user_input == curses.KEY_RESIZE:
            term_dims.update(screen)
        elif user_input in input_to_action:
            input_to_action[user_input]()

if __name__ == '__main__':
    description = 'A less-like pager utility with regex highlighting capabilities'
    epilog = '\n'.join(['Available commands:', 'j: move down one line', 'k: move up one line', 'd: move down half a page', 'u: move up half a page',
        'f: move down a page', 'b: move up a page', 'g: go to beginning of file', 'G: go to end of file', 'H: go to 25% of file', 'M: go to 50% of file',
        'L: go to 75% of file', 'F: tail file', '/: search forwards', '?: search backwards', 'n: continue search', 'N: reverse search', 'q: quit'])
    arg_parser = argparse.ArgumentParser(description=description, epilog=epilog, formatter_class=argparse.RawTextHelpFormatter)
    arg_parser.add_argument('-c', '--config-filepath', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    if len(sys.argv[1:]) == 0:
        arg_parser.print_help()
        arg_parser.exit()
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config_filepath)
