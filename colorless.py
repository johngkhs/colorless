#!/usr/bin/env python

import argparse
import collections
import curses
import os
import re
import sys
import time
import curses.textpad
import textwrap

SEARCH_HIGHLIGHT_COLOR = 256

class SearchTextbox:
    def __init__(self, screen):
        self.screen = screen
        self.textbox = curses.textpad.Textbox(screen)

    def edit(self):
        user_input = self.screen.getch()
        val = ''
        while self.textbox.do_command(user_input):
            if user_input == ord('\n'):
                break
            val += chr(user_input)
            user_input = self.screen.getch()
            self.screen.refresh()
        return val

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

    def at_beginning_of_file(self):
        return self.input_file.tell() == 0

    def prev_full_line(self):
        if self.at_beginning_of_file():
            return
        line = self.prev_char()
        while True:
            if self.at_beginning_of_file():
                break
            char = self.prev_char()
            if char == '\n':
                self.input_file.seek(1, os.SEEK_CUR)
                break
            else:
                line = char + line
        return line

    def prev_line(self):
        if self.at_beginning_of_file():
            return ''
        line = self.prev_full_line()
        wrapped_num_chars_in_line = len(line)
        while wrapped_num_chars_in_line > self.term_dims.cols:
            wrapped_num_chars_in_line -= self.term_dims.cols
        self.input_file.seek(len(line) - wrapped_num_chars_in_line, os.SEEK_CUR)
        return line

    def next_line(self):
        line = self.input_file.readline()
        if len(line) > self.term_dims.cols:
            self.input_file.seek(self.term_dims.cols - len(line), os.SEEK_CUR)
            return line[:self.term_dims.cols]
        return line

    def prev_char(self):
        self.input_file.seek(-1, os.SEEK_CUR)
        char = self.input_file.read(1)
        self.input_file.seek(-1, os.SEEK_CUR)
        return char

    def seek_start(self):
        self.input_file.seek(0, os.SEEK_SET)

    def seek_end(self):
        self.input_file.seek(0, os.SEEK_END)

    def reverse_seek(self, line_count):
        for i in range(line_count):
            self.prev_line()

    def clamp_forward_seekable_line_count(self, line_count):
        current_position = self.input_file.tell()
        END_OF_FILE = ''
        for lines_remaining_in_file in range(self.term_dims.rows + line_count):
            if self.next_line() == END_OF_FILE:
                self.input_file.seek(current_position)
                return max(0, lines_remaining_in_file - self.term_dims.rows)
        self.input_file.seek(current_position)
        return line_count

    def no_clamp_forward_seek(self, line_count):
        for i in range(line_count):
            self.next_line()

    def forward_seek(self, line_count):
        clamped_line_count = self.clamp_forward_seekable_line_count(line_count)
        for i in range(clamped_line_count):
            self.next_line()

    def seek_to_one_page_before_end_of_file(self):
        self.seek_end()
        self.reverse_seek(self.term_dims.rows)

def load_config(config_filepath):
    regex_to_color = collections.OrderedDict()
    if config_filepath:
        config = {}
        execfile(config_filepath, config)
        assert 'regex_to_color' in config, 'Config file is invalid. It must contain a dictionary named regex_to_color of {str: int}.'
        for (regex, color) in config['regex_to_color'].items():
            assert 1 <= color <= curses.COLORS, '\'{0}\': {1} is invalid. Color must be in the range [1, {2}].'.format(regex, color, curses.COLORS)
            regex_to_color[re.compile(r'({0})'.format(regex))] = color
    return regex_to_color

def color_regexes_in_line(line, regex_to_color):
    regex_line = '0' * len(line)
    for regex, color in regex_to_color.items():
        tokens = regex.split(line)
        col = 0
        for index, token in enumerate(tokens):
            token_matches_regex = (index % 2 == 1)
            if token_matches_regex:
                 regex_line = regex_line[:col] + str(color) * len(token) + regex_line[col + len(token):]
            col += len(token)
    return regex_line

def wrap(line, n):
     return [line[i:i+n] for i in range(0, len(line), n)]

def split_on_identical_adjacent(color_line):
    if not color_line:
        return []
    identical_adjacents = [color_line[0]]
    for c in color_line[1:]:
        if c == identical_adjacents[-1][0]:
            identical_adjacents[-1] += c
        else:
            identical_adjacents.append(c)
    return identical_adjacents

def redraw_screen(screen, regex_to_color, file_iterator):
    current_position = file_iterator.input_file.tell()
    screen.move(0, 0)
    row = 0
    while row < file_iterator.term_dims.rows:
        line = input_file.readline()
        if not line:
            break
        color_line = color_regexes_in_line(line, regex_to_color)
        wrapped_lines = wrap(line, file_iterator.term_dims.cols)
        wrapped_color_lines = wrap(color_line, file_iterator.term_dims.cols)
        for (wrapped_line, wrapped_color_line) in zip(wrapped_lines, wrapped_color_lines):
            screen.addstr(row, 0, wrapped_line)
            col = 0
            for split_color in split_on_identical_adjacent(wrapped_color_line):
                if split_color[0] != '0':
                    screen.addstr(row, col, wrapped_line[col:col + len(split_color)], curses.color_pair(int(split_color[0])))
                col += len(split_color)
            row += 1
            if row >= file_iterator.term_dims.rows:
                break
    file_iterator.input_file.seek(current_position)
    screen.addstr(file_iterator.term_dims.rows, 0, ':')
    screen.refresh()

def tail_loop(screen, regex_to_color, file_iterator, term_dims):
    if screen.getch() == curses.KEY_RESIZE:
        term_dims.update(screen)
    file_iterator.seek_to_one_page_before_end_of_file()
    redraw_screen(screen, regex_to_color, file_iterator)
    screen.addstr(term_dims.rows, 0, 'Waiting for data... (interrupt to abort)'[:term_dims.cols - 1])
    screen.refresh()

def enter_tail_mode(screen, regex_to_color, file_iterator, term_dims):
    screen.nodelay(1)
    curses.curs_set(0)
    try:
        while True:
            tail_loop(screen, regex_to_color, file_iterator, term_dims)
            time.sleep(0.1)
    except KeyboardInterrupt:
        screen.clear()
    screen.nodelay(0)
    curses.curs_set(1)

def curses_init_colors():
    DEFAULT_BACKGROUND_COLOR = -1
    curses.use_default_colors()
    for color in range(1, curses.COLORS):
        curses.init_pair(color, color, DEFAULT_BACKGROUND_COLOR)
    curses.init_pair(SEARCH_HIGHLIGHT_COLOR, curses.COLOR_BLACK, curses.COLOR_YELLOW)

def search_forwards(search_regex, file_iterator):
    current_position = file_iterator.input_file.tell()
    line = file_iterator.input_file.readline()
    while True:
        line = file_iterator.input_file.readline()
        if not line:
            file_iterator.input_file.seek(current_position)
            return
        elif search_regex.search(line):
            file_iterator.prev_full_line()
            line_count = file_iterator.clamp_forward_seekable_line_count(file_iterator.term_dims.rows)
            if line_count < file_iterator.term_dims.rows:
                file_iterator.seek_to_one_page_before_end_of_file()
            return

def search_backwards(search_regex, file_iterator):
    current_position = file_iterator.input_file.tell()
    if file_iterator.clamp_forward_seekable_line_count(file_iterator.term_dims.rows) == file_iterator.term_dims.rows - 1:
        file_iterator.no_clamp_forward_seek(file_iterator.term_dims.rows)
    line = file_iterator.prev_full_line()
    while True:
        line = file_iterator.prev_full_line()
        if not line:
            file_iterator.input_file.seek(current_position)
            return
        elif search_regex.search(line):
            return

def main(screen, input_file, config_filepath):
    curses_init_colors()
    regex_to_color = load_config(config_filepath)
    term_dims = TerminalDimensions(screen)
    file_iterator = FileIterator(input_file, term_dims)
    redraw_screen(screen, regex_to_color, file_iterator)
    input_to_action = {ord(key): action for (key, action) in {
        'j' : lambda: file_iterator.forward_seek(1),
        'k' : lambda: file_iterator.reverse_seek(1),
        'd' : lambda: file_iterator.forward_seek(term_dims.rows / 2),
        'u' : lambda: file_iterator.reverse_seek(term_dims.rows / 2),
        'f' : lambda: file_iterator.forward_seek(term_dims.rows),
        'b' : lambda: file_iterator.reverse_seek(term_dims.rows),
        'g' : lambda: file_iterator.seek_start(),
        'G' : lambda: file_iterator.seek_to_one_page_before_end_of_file(),
        'q' : lambda: sys.exit(os.EX_OK)
    }.items()}

    while True:
        user_input = screen.getch()
        if user_input in input_to_action:
            input_to_action[user_input]()
        elif user_input == curses.KEY_RESIZE:
            screen.clear()
            term_dims.update(screen)
        elif user_input == ord('F'):
            enter_tail_mode(screen, regex_to_color, file_iterator, term_dims)
            term_dims.update(screen)
            file_iterator.seek_to_one_page_before_end_of_file()
        elif user_input == ord('/'):
            screen.addstr(file_iterator.term_dims.rows, 0, '/')
            search_textbox = SearchTextbox(screen)
            search_regex = re.compile(search_textbox.edit())
            search_forwards(search_regex, file_iterator)
            highlight_regex = re.compile(r'({0})'.format(search_regex.pattern))
            regex_to_color[highlight_regex] = SEARCH_HIGHLIGHT_COLOR
            input_to_action[ord('n')] = lambda: search_forwards(search_regex, file_iterator)
            input_to_action[ord('N')] = lambda: search_backwards(search_regex, file_iterator)
        elif user_input == ord('?'):
            screen.addstr(file_iterator.term_dims.rows, 0, '?')
            search_textbox = SearchTextbox(screen)
            search_regex = re.compile(search_textbox.edit())
            screen.clear()
            highlight_regex = re.compile(r'({0})'.format(search_regex.pattern))
            regex_to_color[highlight_regex] = SEARCH_HIGHLIGHT_COLOR
            search_backwards(search_regex, file_iterator)
            input_to_action[ord('n')] = lambda: search_backwards(search_regex, file_iterator)
            input_to_action[ord('N')] = lambda: search_forwards(search_regex, file_iterator)
        redraw_screen(screen, regex_to_color, file_iterator)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config)
