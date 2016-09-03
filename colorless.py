#!/usr/bin/env python

import argparse
import collections
import curses
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

class FileIterator:
    def __init__(self, input_file, term_dims):
        self.input_file = input_file
        self.term_dims = term_dims

    def at_beginning_of_file(self):
        return self.input_file.tell() == 0

    def prev_line(self):
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

    def forward_seek(self, line_count):
        clamped_line_count = self.clamp_forward_seekable_line_count(line_count)
        for i in range(clamped_line_count):
            self.next_line()

    def seek_to_one_page_before_end_of_file(self):
        self.seek_end()
        self.reverse_seek(self.term_dims.rows)

def safe_addstr_row_col(screen, row, col, string):
    try:
        screen.addstr(row, col, string)
    except curses.error:
        pass

def safe_addstr(screen, string):
    try:
        screen.addstr(string)
    except curses.error:
        pass

def safe_addstr_color(screen, string, color):
    try:
        screen.addstr(string, color)
    except curses.error:
        pass

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

def increment_cursor(cursor, count, cols):
    while True:
        if cursor[1] + count < cols:
            return (cursor[0], cursor[1] + count)
        else:
            count -= cols
            cursor = (cursor[0] + 1, cursor[1])

def color_regexes_in_line(screen, line, regex_to_color, prev_cursor, new_cursor, file_iterator):
    for regex, color in regex_to_color.items():
        tokens = regex.split(line)
        curr_cursor = prev_cursor
        for index, token in enumerate(tokens):
            screen.move(*curr_cursor)
            token_matches_regex = (index % 2 == 1)
            if token_matches_regex:
                safe_addstr_color(screen, token, curses.color_pair(color))
            curr_cursor = increment_cursor(curr_cursor, len(token), file_iterator.term_dims.cols)
            if curr_cursor[0] > file_iterator.term_dims.rows:
                break
    screen.move(*new_cursor)

def redraw_screen(screen, regex_to_color, file_iterator):
    current_position = file_iterator.input_file.tell()
    screen.move(0, 0)
    while screen.getyx()[0] < file_iterator.term_dims.rows:
        line = input_file.readline()
        if not line:
            break
        prev_cursor = screen.getyx()
        safe_addstr(screen, line)
        new_cursor = screen.getyx()
        color_regexes_in_line(screen, line, regex_to_color, prev_cursor, new_cursor, file_iterator)
    file_iterator.input_file.seek(current_position)
    screen.move(file_iterator.term_dims.rows, 0)
    screen.clrtoeol()
    safe_addstr_row_col(screen, file_iterator.term_dims.rows, 0, ':')
    screen.refresh()

def draw_last_page(screen, regex_to_color, file_iterator):
    file_iterator.seek_end()
    file_iterator.seek_to_one_page_before_end_of_file()
    redraw_screen(screen, regex_to_color, file_iterator)
    safe_addstr_row_col(screen, file_iterator.term_dims.rows, 0, 'Waiting for data... (interrupt to abort)')
    screen.refresh()

def tail_loop(screen, regex_to_color, file_iterator, term_dims):
    while True:
        if screen.getch() == curses.KEY_RESIZE:
            term_dims.update(screen)
            file_iterator.seek_to_one_page_before_end_of_file()
            redraw_screen(screen, regex_to_color, file_iterator)
        draw_last_page(screen, regex_to_color, file_iterator)
        time.sleep(0.1)

def enter_tail_mode(screen, regex_to_color, file_iterator, term_dims):
    screen.nodelay(1)
    curses.curs_set(0)
    try:
        tail_loop(screen, regex_to_color, file_iterator, term_dims)
    except KeyboardInterrupt:
        pass
    screen.nodelay(0)
    curses.curs_set(1)

def curses_init_colors():
    DEFAULT_BACKGROUND_COLOR = -1
    curses.use_default_colors()
    for color in range(1, curses.COLORS):
        curses.init_pair(color, color, DEFAULT_BACKGROUND_COLOR)

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
        redraw_screen(screen, regex_to_color, file_iterator)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config)
