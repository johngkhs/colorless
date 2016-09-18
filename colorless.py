#!/usr/bin/env python

import argparse
import collections
import copy
import curses
import itertools
import os
import re
import sys
import time

class SearchHistory:
    def __init__(self):
        self.HIGHLIGHT_COLOR = 256
        curses.init_pair(self.HIGHLIGHT_COLOR, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        self.most_recent_search_query = None
        self.filepath = os.path.join(os.path.expanduser('~'), '.colorless_history')
        with open(self.filepath, 'a+') as search_history_file:
            search_history_file.seek(0)
            self.search_queries = [line.rstrip('\n') for line in search_history_file.readlines()]

    def get_most_recent_search_query(self):
        return self.most_recent_search_query

    def add_search_query(self, search_query):
        self.most_recent_search_query = search_query
        self.search_queries.insert(0, search_query)
        self.search_queries = list(collections.OrderedDict.fromkeys(self.search_queries))
        with open(self.filepath, 'w') as search_history_file:
            MAX_HISTORY_LINES = 50
            search_history_file.writelines(s + '\n' for s in self.search_queries[:MAX_HISTORY_LINES - 1])

class TerminalDimensions:
    def __init__(self, screen):
        self.update(screen)

    def update(self, screen):
        term_dimensions = screen.getmaxyx()
        self.rows = term_dimensions[0] - 1
        self.cols = term_dimensions[1]

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
                assert self.input_file.tell() == 0, 'File contained a line > {0} characters'.format(CHUNK_SIZE)
                yield lines[0]
                yield ''
                return
            assert len(lines) > 1, 'File contained a line > {0} characters'.format(CHUNK_SIZE)
            for line in reversed(lines[1:]):
                self.input_file.seek(-len(line), os.SEEK_CUR)
                yield line

    def next_line(self):
        return self.input_file.readline()

    def seek_to_percentage_of_file(self, percentage):
        assert 0.0 <= percentage <= 1.0
        self.input_file.seek(0, os.SEEK_END)
        total_bytes_in_file = self.input_file.tell()
        self.input_file.seek(percentage * total_bytes_in_file)
        next(self.prev_line_iterator())
        self.clamp_position_to_one_page_before_end_of_file()

    def seek_to_start_of_file(self):
        self.input_file.seek(0, os.SEEK_SET)

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
        self.input_file.seek(0, os.SEEK_END)
        self.seek_prev_wrapped_lines(self.term_dims.rows)

    def clamp_position_to_one_page_before_end_of_file(self):
        position = self.input_file.tell()
        self.seek_to_one_page_before_end_of_file()
        self.input_file.seek(min(position, input_file.tell()))

    def seek_next_wrapped_lines_and_clamp_position(self, count):
        self.seek_next_wrapped_lines(count)
        self.clamp_position_to_one_page_before_end_of_file()


    def search_forwards(self, search_query):
        position = self.input_file.tell()
        line = self.next_line()
        while True:
            line = self.next_line()
            if not line:
                self.input_file.seek(position)
                return
            elif re.search(search_query, line):
                next(self.prev_line_iterator())
                self.clamp_position_to_one_page_before_end_of_file()
                return

    def search_backwards(self, search_query):
        position = self.input_file.tell()
        for line in self.prev_line_iterator():
            if not line:
                self.input_file.seek(position)
                return
            elif re.search(search_query, line):
                return

def load_config(config_filepath):
    regex_to_color = collections.OrderedDict()
    if config_filepath:
        config = {}
        execfile(config_filepath, config)
        assert 'regex_to_color' in config, 'Config file is invalid. It must contain a dictionary named regex_to_color of {str: int}.'
        for (regex, color) in config['regex_to_color'].items():
            assert 1 <= color <= 255, '\'{0}\': {1} is invalid. Color must be in the range [1, 255].'.format(regex, color)
            regex_to_color[r'({0})'.format(regex)] = color
            DEFAULT_BACKGROUND_COLOR = -1
            curses.init_pair(color, color, DEFAULT_BACKGROUND_COLOR)
    return regex_to_color

def color_regexes_in_line(line, regex_to_color):
    regex_line = [0] * len(line)
    for regex, color in regex_to_color.items():
        tokens = re.split(regex, line)
        col = 0
        for index, token in enumerate(tokens):
            token_matches_regex = (index % 2 == 1)
            if token_matches_regex:
                regex_line[col:col + len(token)] = [color] * len(token)
            col += len(token)
    return regex_line

def wrap(line, n):
     return [line[i:i+n] for i in range(0, len(line), n)]

def redraw_screen(screen, regex_to_color, file_iterator, search_history, prompt):
    new_regex_to_color = copy.deepcopy(regex_to_color)
    if search_history.get_most_recent_search_query():
        new_regex_to_color[r'({0})'.format(search_history.get_most_recent_search_query())] = search_history.HIGHLIGHT_COLOR
    position = file_iterator.input_file.tell()
    screen.move(0, 0)
    row = 0
    while row < file_iterator.term_dims.rows:
        line = file_iterator.next_line()
        if not line:
            break
        color_line = color_regexes_in_line(line, new_regex_to_color)
        wrapped_lines = wrap(line, file_iterator.term_dims.cols)
        wrapped_color_lines = wrap(color_line, file_iterator.term_dims.cols)
        for (wrapped_line, wrapped_color_line) in zip(wrapped_lines, wrapped_color_lines):
            screen.addstr(row, 0, wrapped_line)
            col = 0
            for color, length in [(color, len(list(group_iter))) for color, group_iter in itertools.groupby(wrapped_color_line)]:
                if color != 0:
                    screen.addstr(row, col, wrapped_line[col:col + length], curses.color_pair(color))
                col += length
            row += 1
            if row >= file_iterator.term_dims.rows:
                break
    file_iterator.input_file.seek(position)
    screen.move(file_iterator.term_dims.rows, 1)
    screen.clrtoeol()
    screen.addstr(file_iterator.term_dims.rows, 0, prompt)
    screen.refresh()

def tail_loop(screen, regex_to_color, file_iterator, search_history, term_dims):
    if screen.getch() == curses.KEY_RESIZE:
        term_dims.update(screen)
    file_iterator.seek_to_one_page_before_end_of_file()
    redraw_screen(screen, regex_to_color, file_iterator, search_history, 'Waiting for data... (interrupt to abort)'[:term_dims.cols - 2])

def enter_tail_mode(screen, regex_to_color, file_iterator, search_history, term_dims):
    screen.nodelay(1)
    curses.curs_set(0)
    try:
        while True:
            tail_loop(screen, regex_to_color, file_iterator, search_history, term_dims)
            time.sleep(0.1)
    except KeyboardInterrupt:
        screen.clear()
    screen.nodelay(0)
    curses.curs_set(1)

def get_search_query_input(screen, term_dims, search_history):
    search_query = ''
    search_queries_index = 0
    while True:
        user_input = screen.getch()
        if user_input == curses.KEY_BACKSPACE or user_input == 127:
            search_query = search_query[:-1]
        elif user_input == curses.KEY_UP:
            if search_queries_index < len(search_history.search_queries):
                search_query = search_history.search_queries[search_queries_index]
                search_queries_index += 1
        elif user_input == curses.KEY_DOWN:
            if search_queries_index > 0:
                search_queries_index -= 1
                search_query = search_history.search_queries[search_queries_index]
        elif 0 <= user_input <= 255:
            if chr(user_input) == '\n':
                break
            else:
                search_query += chr(user_input)
        screen.move(term_dims.rows, 1)
        screen.clrtoeol()
        screen.addstr(term_dims.rows, 1, search_query)
        screen.refresh()
    return search_query

def enter_search_mode(screen, regex_to_color, term_dims, search_history, search_char):
    screen.addstr(term_dims.rows, 0, search_char)
    curses.echo()
    try:
        search_query = get_search_query_input(screen, term_dims, search_history)
    except KeyboardInterrupt:
        curses.noecho()
        screen.clear()
        return None
    curses.noecho()
    screen.clear()
    return search_query

def main(screen, input_file, config_filepath):
    curses.use_default_colors()
    regex_to_color = load_config(config_filepath)
    search_history = SearchHistory()
    term_dims = TerminalDimensions(screen)
    file_iterator = FileIterator(input_file, term_dims)
    input_to_action = {ord(key): action for (key, action) in {
        'j' : lambda: file_iterator.seek_next_wrapped_lines_and_clamp_position(1),
        'k' : lambda: file_iterator.seek_prev_wrapped_lines(1),
        'd' : lambda: file_iterator.seek_next_wrapped_lines_and_clamp_position(term_dims.rows / 2),
        'u' : lambda: file_iterator.seek_prev_wrapped_lines(term_dims.rows / 2),
        'f' : lambda: file_iterator.seek_next_wrapped_lines_and_clamp_position(term_dims.rows),
        'b' : lambda: file_iterator.seek_prev_wrapped_lines(term_dims.rows),
        'g' : lambda: file_iterator.seek_to_start_of_file(),
        'G' : lambda: file_iterator.seek_to_one_page_before_end_of_file(),
        'q' : lambda: sys.exit(os.EX_OK)
    }.items()}

    user_input_number = ''
    while True:
        redraw_screen(screen, regex_to_color, file_iterator, search_history, ':' + user_input_number)
        try:
            user_input = screen.getch()
        except KeyboardInterrupt:
            user_input_number = ''
            continue
        if user_input in [ord(str(i)) for i in range(10)]:
            user_input_number += chr(user_input)
        elif user_input in input_to_action:
            counter = int(user_input_number) if user_input_number else 1
            for _ in range(counter):
                input_to_action[user_input]()
            user_input_number = ''
        elif user_input == curses.KEY_RESIZE:
            screen.clear()
            term_dims.update(screen)
        elif user_input == ord('F'):
            enter_tail_mode(screen, regex_to_color, file_iterator, search_history, term_dims)
            term_dims.update(screen)
            file_iterator.seek_to_one_page_before_end_of_file()
        elif user_input == ord('/'):
            new_search_query = enter_search_mode(screen, regex_to_color, term_dims, search_history, '/')
            if new_search_query:
                search_query = new_search_query
                search_history.add_search_query(search_query)
                file_iterator.search_forwards(search_query)
                input_to_action[ord('n')] = lambda: file_iterator.search_forwards(search_query)
                input_to_action[ord('N')] = lambda: file_iterator.search_backwards(search_query)
        elif user_input == ord('?'):
            new_search_query = enter_search_mode(screen, regex_to_color, term_dims, search_history, '?')
            if new_search_query:
                search_query = new_search_query
                search_history.add_search_query(search_query)
                file_iterator.search_backwards(search_query)
                input_to_action[ord('n')] = lambda: file_iterator.search_backwards(search_query)
                input_to_action[ord('N')] = lambda: file_iterator.search_forwards(search_query)
        elif user_input == ord('%'):
            percentage_of_file = 0.01 * min(100, int(user_input_number)) if user_input_number else 0.0
            file_iterator.seek_to_percentage_of_file(percentage_of_file)
            user_input_number = ''
        else:
            user_input_number = ''

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config)
