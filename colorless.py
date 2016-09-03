#!/usr/bin/env python

import argparse
import collections
import curses
import os
import re
import sys
import time

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

def increment_cursor(cursor, count, term_num_cols):
    while True:
        if cursor[1] + count < term_num_cols:
            return (cursor[0], cursor[1] + count)
        else:
            count -= term_num_cols
            cursor = (cursor[0] + 1, cursor[1])

def color_regexes_in_line(screen, line, regex_to_color, prev_cursor, new_cursor, term_num_rows, term_num_cols):
    for regex, color in regex_to_color.items():
        tokens = regex.split(line)
        curr_cursor = prev_cursor
        for index, token in enumerate(tokens):
            screen.move(*curr_cursor)
            token_matches_regex = (index % 2 == 1)
            if token_matches_regex:
                safe_addstr_color(screen, token, curses.color_pair(color))
            curr_cursor = increment_cursor(curr_cursor, len(token), term_num_cols)
            if curr_cursor[0] >= term_num_rows:
                break
    screen.move(*new_cursor)

def read_char_backwards(input_file):
    input_file.seek(-1, os.SEEK_CUR)
    char = input_file.read(1)
    input_file.seek(-1, os.SEEK_CUR)
    return char

def at_beginning_of_file(input_file):
    BEGINNING_OF_FILE = 0
    if input_file.tell() == BEGINNING_OF_FILE:
        return True
    return False

def readline_backwards_with_wrapping(input_file, term_num_cols):
    # TODO fix function
    if at_beginning_of_file(input_file):
        return
    line = read_char_backwards(input_file)
    while True:
        if at_beginning_of_file(input_file):
            break
        char = read_char_backwards(input_file)
        if char == '\n':
            input_file.seek(1, os.SEEK_CUR)
            break
        else:
            line = char + line

    wrapped_num_chars_in_line = len(line)
    while wrapped_num_chars_in_line > term_num_cols:
        wrapped_num_chars_in_line -= term_num_cols
    input_file.seek(len(line) - wrapped_num_chars_in_line, os.SEEK_CUR)
    return line

def readline_forwards_with_wrapping(input_file, term_num_cols):
    line = input_file.readline()
    if len(line) > term_num_cols:
        input_file.seek(term_num_cols - len(line), os.SEEK_CUR)
        return line[:term_num_cols]
    return line

def redraw_screen(screen, regex_to_color, input_file, term_num_rows, term_num_cols):
    current_position = input_file.tell()
    screen.move(0, 0)
    while screen.getyx()[0] < term_num_rows:
        line = input_file.readline()
        if not line:
            break
        prev_cursor = screen.getyx()
        safe_addstr(screen, line)
        new_cursor = screen.getyx()
        color_regexes_in_line(screen, line, regex_to_color, prev_cursor, new_cursor, term_num_rows, term_num_cols)
    input_file.seek(current_position)
    screen.move(term_num_rows, 0)
    screen.clrtoeol()
    safe_addstr_row_col(screen, term_num_rows, 0, ':')
    screen.refresh()

def seek_backwards(line_count, input_file, term_num_cols):
    for i in range(line_count):
        line = readline_backwards_with_wrapping(input_file, term_num_cols)

def clamp_forward_seekable_line_count(line_count, input_file, term_num_rows, term_num_cols):
    current_position = input_file.tell()
    END_OF_FILE = ''
    for lines_remaining_in_file in range(term_num_rows + line_count):
        if readline_forwards_with_wrapping(input_file, term_num_cols) == END_OF_FILE:
            input_file.seek(current_position)
            return max(0, lines_remaining_in_file - term_num_rows)
    input_file.seek(current_position)
    return line_count

def seek_forwards(line_count, input_file, term_num_rows, term_num_cols):
    clamped_line_count = clamp_forward_seekable_line_count(line_count, input_file, term_num_rows, term_num_cols)
    for i in range(clamped_line_count):
        readline_forwards_with_wrapping(input_file, term_num_cols)

def get_term_dimensions(screen):
    return (screen.getmaxyx()[0] - 1, screen.getmaxyx()[1])

def draw_last_page(screen, regex_to_color, input_file, term_num_rows, term_num_cols):
    input_file.seek(0, os.SEEK_END)
    seek_to_one_page_before_end_of_file(input_file, term_num_rows, term_num_cols)
    redraw_screen(screen, regex_to_color, input_file, term_num_rows, term_num_cols)
    safe_addstr_row_col(screen, term_num_rows, 0, 'Waiting for data... (interrupt to abort)')
    screen.refresh()

def tail_loop(screen, regex_to_color, input_file, term_num_rows, term_num_cols):
    while True:
        if screen.getch() == curses.KEY_RESIZE:
            term_num_rows, term_num_cols = get_term_dimensions(screen)
            seek_to_one_page_before_end_of_file(input_file, term_num_rows, term_num_cols)
            redraw_screen(screen, regex_to_color, input_file, term_num_rows, term_num_cols)
        draw_last_page(screen, regex_to_color, input_file, term_num_rows, term_num_cols)
        time.sleep(0.1)

def seek_to_one_page_before_end_of_file(input_file, term_num_rows, term_num_cols):
    input_file.seek(0, os.SEEK_END)
    seek_backwards(term_num_rows, input_file, term_num_cols)

def enter_tail_mode(screen, regex_to_color, input_file, term_num_rows, term_num_cols):
    screen.nodelay(1)
    curses.curs_set(0)
    try:
        tail_loop(screen, regex_to_color, input_file, term_num_rows, term_num_cols)
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
    term_num_rows, term_num_cols = get_term_dimensions(screen)
    redraw_screen(screen, regex_to_color, input_file, term_num_rows, term_num_cols)
    input_to_action = {ord(key): action for (key, action) in {
        'j' : lambda: seek_forwards(1, input_file, term_num_rows, term_num_cols),
        'k' : lambda: seek_backwards(1, input_file, term_num_cols),
        'd' : lambda: seek_forwards(term_num_rows / 2, input_file, term_num_rows, term_num_cols),
        'u' : lambda: seek_backwards(term_num_rows / 2, input_file, term_num_cols),
        'f' : lambda: seek_forwards(term_num_rows, input_file, term_num_rows, term_num_cols),
        'b' : lambda: seek_backwards(term_num_rows, input_file, term_num_cols),
        'g' : lambda: input_file.seek(0, os.SEEK_SET),
        'G' : lambda: seek_to_one_page_before_end_of_file(input_file, term_num_rows, term_num_cols),
        'q' : lambda: sys.exit(os.EX_OK)
    }.items()}

    while True:
        user_input = screen.getch()
        if user_input in input_to_action:
            input_to_action[user_input]()
        elif user_input == curses.KEY_RESIZE:
            screen.clear()
            term_num_rows, term_num_cols = get_term_dimensions(screen)
        elif user_input == ord('F'):
            enter_tail_mode(screen, regex_to_color, input_file, term_num_rows, term_num_cols)
            term_num_rows, term_num_cols = get_term_dimensions(screen)
            seek_to_one_page_before_end_of_file(input_file, term_num_rows, term_num_cols)
        redraw_screen(screen, regex_to_color, input_file, term_num_rows, term_num_cols)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config)
