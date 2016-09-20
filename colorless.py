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

    def get_last_query_regex(self):
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
            if len(lines) == 1:
                self.input_file.seek(-(len(lines[0])), os.SEEK_CUR)
                if self.input_file.tell() == 0:
                    yield lines[0]
                    yield ''
                    return
                else:
                    CHUNK_SIZE *= 2
                    self.input_file.seek(chunk_size, os.SEEK_CUR)
                    continue
            else:
                for line in reversed(lines[1:]):
                    self.input_file.seek(-len(line), os.SEEK_CUR)
                    yield line

    def next_line(self):
        return self.input_file.readline()

    def get_file_size_in_bytes(self):
        position = self.input_file.tell()
        self.seek_to_end_of_file()
        file_size_in_bytes = self.input_file.tell()
        self.input_file.seek(position)
        return file_size_in_bytes

    def seek_to_percentage_of_file(self, percentage):
        assert 0.0 <= percentage <= 1.0
        file_size_in_bytes = self.get_file_size_in_bytes()
        self.input_file.seek(percentage * file_size_in_bytes)
        next(self.prev_line_iterator())
        self.clamp_position_to_one_page_before_end_of_file()

    def seek_to_start_of_file(self):
        self.input_file.seek(0, os.SEEK_SET)

    def seek_to_end_of_file(self):
        self.input_file.seek(0, os.SEEK_END)

    def seek_next_wrapped_line(self):
        line = self.next_line()
        if len(line) > self.term_dims.cols:
            self.input_file.seek(self.term_dims.cols - len(line), os.SEEK_CUR)

    def seek_prev_wrapped_line(self):
        line = next(self.prev_line_iterator())
        wrapped_lines = wrap(line, self.term_dims.cols)
        for wrapped_line in wrapped_lines[:-1]:
            self.input_file.seek(len(wrapped_line), os.SEEK_CUR)

    def seek_next_wrapped_lines(self, count):
        for i in range(count):
            self.seek_next_wrapped_line()

    def seek_prev_wrapped_lines(self, count):
        for i in range(count):
            self.seek_prev_wrapped_line()

    def seek_to_one_page_before_end_of_file(self):
        self.seek_to_end_of_file()
        self.seek_prev_wrapped_lines(self.term_dims.rows)

    def clamp_position_to_one_page_before_end_of_file(self):
        position = self.input_file.tell()
        self.seek_to_one_page_before_end_of_file()
        self.input_file.seek(min(position, input_file.tell()))

    def seek_next_wrapped_lines_and_clamp_position(self, count):
        self.seek_next_wrapped_lines(count)
        self.clamp_position_to_one_page_before_end_of_file()

    def search_forwards(self, search_regex):
        position = self.input_file.tell()
        try:
            line = self.next_line()
            while True:
                line = self.next_line()
                if not line:
                    self.input_file.seek(position)
                    return
                elif search_regex.search(line):
                    next(self.prev_line_iterator())
                    self.clamp_position_to_one_page_before_end_of_file()
                    return
        except KeyboardInterrupt:
            self.input_file.seek(position)

    def search_backwards(self, search_regex):
        position = self.input_file.tell()
        try:
            for line in self.prev_line_iterator():
                if not line:
                    self.input_file.seek(position)
                    return
                elif search_regex.search(line):
                    return
        except KeyboardInterrupt:
            self.input_file.seek(position)

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
            regex_to_color[self.search_history.get_last_query_regex()] = self.SEARCH_COLOR
        return regex_to_color.items()

def wrap(line, n):
     return [line[i:i+n] for i in range(0, len(line), n)]

def redraw_screen(screen, regex_to_color, file_iter, prompt):
    position = file_iter.input_file.tell()
    screen.move(0, 0)
    row = 0
    while row < file_iter.term_dims.rows:
        line = file_iter.next_line()
        if not line:
            break
        color_line = regex_to_color.to_colored_line(line)
        wrapped_lines = wrap(line, file_iter.term_dims.cols)
        wrapped_color_lines = wrap(color_line, file_iter.term_dims.cols)
        for (wrapped_line, wrapped_color_line) in zip(wrapped_lines, wrapped_color_lines):
            screen.addstr(row, 0, wrapped_line)
            col = 0
            for color, length in [(color, len(list(group_iter))) for color, group_iter in itertools.groupby(wrapped_color_line)]:
                if color != 0:
                    screen.addstr(row, col, wrapped_line[col:col + length], curses.color_pair(color))
                col += length
            row += 1
            if row >= file_iter.term_dims.rows:
                break
    file_iter.input_file.seek(position)
    screen.move(file_iter.term_dims.rows, 1)
    screen.clrtoeol()
    screen.addstr(file_iter.term_dims.rows, 0, prompt)
    screen.refresh()

def tail_loop(screen, regex_to_color, file_iter, term_dims):
    if screen.getch() == curses.KEY_RESIZE:
        term_dims.update(screen)
    file_iter.seek_to_one_page_before_end_of_file()
    redraw_screen(screen, regex_to_color, file_iter, 'Waiting for data... (interrupt to abort)'[:term_dims.cols - 2])

def tail_mode(screen, regex_to_color, file_iter, term_dims):
    try:
        screen.nodelay(1)
        curses.curs_set(0)
        tail_loop(screen, regex_to_color, file_iter, term_dims)
        file_size_in_bytes = file_iter.get_file_size_in_bytes()
        while True:
            if file_size_in_bytes != file_iter.get_file_size_in_bytes():
                tail_loop(screen, regex_to_color, file_iter, term_dims)
                file_size_in_bytes = file_iter.get_file_size_in_bytes()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        screen.clear()
        screen.nodelay(0)
        curses.curs_set(1)
    term_dims.update(screen)
    file_iter.seek_to_one_page_before_end_of_file()

class SearchMode:
    def __init__(self, screen, term_dims, file_iter, search_history):
        self.screen = screen
        self.term_dims = term_dims
        self.file_iter = file_iter
        self.search_history = search_history
        self.next_function = lambda: None
        self.prev_function = lambda: None

    def run(self, search_char):
        try:
            self.screen.addstr(self.term_dims.rows, 0, search_char)
            curses.echo()
            search_query = self.__get_user_inputted_search_query()
        except KeyboardInterrupt:
            return
        finally:
            curses.noecho()
            self.screen.clear()
        self.search_history.add(search_query)
        search_regex = self.search_history.get_last_query_regex()
        if search_char == '/':
            self.file_iter.search_forwards(search_regex)
            self.next_function = lambda: self.file_iter.search_forwards(search_regex)
            self.prev_function = lambda: self.file_iter.search_backwards(search_regex)
        else:
            self.file_iter.search_backwards(search_regex)
            self.next_function = lambda: self.file_iter.search_backwards(search_regex)
            self.prev_function = lambda: self.file_iter.search_forwards(search_regex)

    def next(self):
        self.next_function()

    def prev(self):
        self.prev_function()

    def __get_user_inputted_search_query(self):
        search_query = ''
        search_queries_iter = self.search_history.to_iterator()
        KEY_DELETE = 127
        input_to_search_query = {
            KEY_DELETE : lambda: search_query[:-1],
            curses.KEY_BACKSPACE : lambda: search_query[:-1],
            curses.KEY_UP : lambda: search_queries_iter.next(),
            curses.KEY_DOWN : lambda: search_queries_iter.prev()
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

def main(screen, input_file, config_filepath):
    curses.use_default_colors()
    search_history = SearchHistory()
    regex_to_color = RegexToColor(config_filepath, search_history)
    term_dims = TerminalDimensions(screen)
    file_iter = FileIterator(input_file, term_dims)
    search_mode = SearchMode(screen, term_dims, file_iter, search_history)
    input_to_action = {ord(key): action for (key, action) in {
        'j' : lambda: file_iter.seek_next_wrapped_lines_and_clamp_position(1),
        'k' : lambda: file_iter.seek_prev_wrapped_lines(1),
        'd' : lambda: file_iter.seek_next_wrapped_lines_and_clamp_position(term_dims.rows / 2),
        'u' : lambda: file_iter.seek_prev_wrapped_lines(term_dims.rows / 2),
        'f' : lambda: file_iter.seek_next_wrapped_lines_and_clamp_position(term_dims.rows),
        'b' : lambda: file_iter.seek_prev_wrapped_lines(term_dims.rows),
        'g' : lambda: file_iter.seek_to_start_of_file(),
        'G' : lambda: file_iter.seek_to_one_page_before_end_of_file(),
        'H' : lambda: file_iter.seek_to_percentage_of_file(0.25),
        'M' : lambda: file_iter.seek_to_percentage_of_file(0.50),
        'L' : lambda: file_iter.seek_to_percentage_of_file(0.75),
        'F' : lambda: tail_mode(screen, regex_to_color, file_iter, term_dims),
        '/' : lambda: search_mode.run('/'),
        '?' : lambda: search_mode.run('?'),
        'n' : lambda: search_mode.next(),
        'N' : lambda: search_mode.prev(),
        'q' : lambda: sys.exit(os.EX_OK)
    }.items()}

    while True:
        redraw_screen(screen, regex_to_color, file_iter, ':')
        try:
            user_input = screen.getch()
        except KeyboardInterrupt:
            continue
        if user_input == curses.KEY_RESIZE:
            term_dims.update(screen)
        elif user_input in input_to_action:
            input_to_action[user_input]()

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config-filepath', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config_filepath)
