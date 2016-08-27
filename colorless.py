#!/usr/bin/env python

import argparse
import collections
import curses
import os
import re
import sys
import time

def load_config(config_filepath):
    regex_to_color = collections.OrderedDict()
    if config_filepath:
        config = {}
        execfile(config_filepath, config)
        for color_number, (regex, color) in enumerate(config['regex_to_color'].items(), start = 1):
            DEFAULT_BACKGROUND_COLOR = -1
            curses.init_pair(color_number, color, DEFAULT_BACKGROUND_COLOR)
            regex_to_color[re.compile(regex)] = color_number
    return regex_to_color

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

def color_regexes_in_line(stdscr, row_index, line, regex_to_color):
    for regex, color in regex_to_color.items():
        tokens = regex.split(line)
        curr_col = 0
        for index, token in enumerate(tokens):
            token_matches_regex = (index % 2 == 1)
            if token_matches_regex:
                stdscr.addstr(row_index, curr_col, token, curses.color_pair(color))
            curr_col += len(token)

def redraw_screen_forwards(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols):
    current_position = input_file.tell()
    for row_index in range(term_num_rows):
        line = readline_forwards_with_wrapping(input_file, term_num_cols)
        if not line:
            break
        stdscr.addstr(row_index, 0, line)
        color_regexes_in_line(stdscr, row_index, line, regex_to_color)
    input_file.seek(current_position)
    stdscr.addstr(term_num_rows, 0, ':')
    stdscr.refresh()

def redraw_screen_backwards(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols):
    current_position = input_file.tell()
    for row_index in reversed(range(term_num_rows)):
        line = readline_backwards_with_wrapping(input_file, term_num_cols)
        if not line:
            break
        stdscr.addstr(row_index, 0, line)
        color_regexes_in_line(stdscr, row_index, line, regex_to_color)
    input_file.seek(current_position)
    stdscr.addstr(term_num_rows, 0, 'Waiting for data... (interrupt to abort)')
    stdscr.refresh()

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

def get_term_dimensions(stdscr):
    return tuple(n - 1 for n in stdscr.getmaxyx())

def draw_lines_appended_to_file(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols):
    while True:
        line = readline_forwards_with_wrapping(input_file, term_num_rows)
        if line == '':
            break
        stdscr.scroll(1)
        stdscr.addstr(term_num_rows - 1, 0, line)
        color_regexes_in_line(stdscr, term_num_rows - 1, line, regex_to_color)
    stdscr.addstr(term_num_rows, 0, 'Waiting for data... (interrupt to abort)')
    stdscr.refresh()

def tail_loop(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols):
    while True:
        time.sleep(0.1)
        if stdscr.getch() == curses.KEY_RESIZE:
            term_num_rows, term_num_cols = get_term_dimensions(stdscr)
            stdscr.clear()
            redraw_screen_backwards(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols)
        draw_lines_appended_to_file(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols)

def seek_to_one_page_before_end_of_file(input_file, term_num_rows, term_num_cols):
    input_file.seek(0, os.SEEK_END)
    seek_backwards(term_num_rows, input_file, term_num_cols)

def enable_tail_mode(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols):
    input_file.seek(0, os.SEEK_END)
    stdscr.clear()
    redraw_screen_backwards(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols)
    stdscr.nodelay(1)
    curses.curs_set(0)
    try:
        tail_loop(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols)
    except KeyboardInterrupt:
        pass
    stdscr.clear()
    stdscr.nodelay(0)
    curses.curs_set(1)

def main(stdscr, input_file, config_filepath):
    curses.use_default_colors()
    regex_to_color = load_config(config_filepath)
    term_num_rows, term_num_cols = get_term_dimensions(stdscr)
    stdscr.scrollok(True)
    redraw_screen_forwards(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols)
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
        user_input = stdscr.getch()
        if user_input in input_to_action:
            input_to_action[user_input]()
        elif user_input == curses.KEY_RESIZE:
            stdscr.clear()
            term_num_rows, term_num_cols = get_term_dimensions(stdscr)
        elif user_input == ord('F'):
            enable_tail_mode(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols)
            term_num_rows, term_num_cols = get_term_dimensions(stdscr)
            seek_to_one_page_before_end_of_file(input_file, term_num_rows, term_num_cols)
        redraw_screen_forwards(stdscr, regex_to_color, input_file, term_num_rows, term_num_cols)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config)
