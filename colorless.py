#!/usr/bin/env python

import argparse
import collections
import curses
import os
import re
import sys

def load_config(config_filepath):
    if config_filepath:
        config = {}
        execfile(config_filepath, config)
        regex_to_color = collections.OrderedDict()
        for color_number, (regex, color) in enumerate(config['regex_to_color'].items(), start = 1):
            DEFAULT_BACKGROUND_COLOR = -1
            curses.init_pair(color_number, color, DEFAULT_BACKGROUND_COLOR)
            regex_to_color[re.compile(regex)] = color_number
        return regex_to_color
    else:
        return collections.OrderedDict()

def readline_with_line_wrapping(input_file, term_num_cols):
    line = input_file.readline()
    if len(line) > term_num_cols:
        input_file.seek(term_num_cols - len(line), os.SEEK_CUR)
        return line[:term_num_cols]
    return line

def display_screen(window, regex_to_color, input_file, term_num_rows, term_num_cols):
    current_position = input_file.tell()
    for i in range(term_num_rows):
        line = readline_with_line_wrapping(input_file, term_num_cols)
        window.addstr(i, 0, line)
        for regex, color in regex_to_color.items():
            tokens = regex.split(line)
            start_index = 0
            for index, token in enumerate(tokens):
                if index % 2 == 1:
                    window.addstr(i, start_index, token, curses.color_pair(color))
                start_index += len(token)
    input_file.seek(current_position)
    window.refresh()

def seek_up(input_file, num_lines):
    BEGINNING_OF_FILE = 0
    while num_lines + 1 > 0:
        if input_file.tell() == BEGINNING_OF_FILE:
            return
        input_file.seek(-1, os.SEEK_CUR)
        char = input_file.read(1)
        input_file.seek(-1, os.SEEK_CUR)
        if char == '\n':
            num_lines -= 1
    input_file.seek(1, os.SEEK_CUR)

def clamp_num_lines(input_file, num_lines, term_num_rows, term_num_cols):
    current_position = input_file.tell()
    clamped_num_lines = 0
    while term_num_rows > 0:
        if readline_with_line_wrapping(input_file, term_num_cols) == '':
            break
        term_num_rows -= 1

    if term_num_rows == 0:
        for i in range(1, num_lines + 1):
            if readline_with_line_wrapping(input_file, term_num_cols) == '':
                break
            clamped_num_lines = i

    input_file.seek(current_position)
    return clamped_num_lines

def seek_down(input_file, num_lines, term_num_rows, term_num_cols):
    num_lines =  clamp_num_lines(input_file, num_lines, term_num_rows, term_num_cols)

    END_OF_FILE = ''
    while num_lines > 0:
        char = input_file.read(1)
        if char == END_OF_FILE:
            return
        elif char == '\n':
            num_lines -= 1

def go_to_end_of_file(input_file, term_num_rows):
    input_file.seek(0, os.SEEK_END)
    seek_up(input_file, term_num_rows)

def main(window, input_file, config_filepath):
    curses.use_default_colors()
    regex_to_color = load_config(config_filepath)
    term_num_rows, term_num_cols = tuple(n - 1 for n in window.getmaxyx())
    # window.scrollok(True)
    # window.setscrreg(0, term_num_rows)
    display_screen(window, regex_to_color, input_file, term_num_rows, term_num_cols)
    input_to_action = {
        'j' : lambda: seek_down(input_file, 1, term_num_rows, term_num_cols),
        'k' : lambda: seek_up(input_file, 1),
        'd' : lambda: seek_down(input_file, term_num_rows / 2, term_num_rows, term_num_cols),
        'u' : lambda: seek_up(input_file, term_num_rows / 2),
        'f' : lambda: seek_down(input_file, term_num_rows, term_num_rows, term_num_cols),
        'b' : lambda: seek_up(input_file, term_num_rows),
        'g' : lambda: input_file.seek(0, os.SEEK_SET),
        'G' : lambda: go_to_end_of_file(input_file, term_num_rows),
        'q' : lambda: sys.exit(0)
    }

    while True:
        user_input = window.getch()
        if 0 <= user_input <= 255 and chr(user_input) in input_to_action:
            input_to_action[chr(user_input)]()
        elif user_input == curses.KEY_RESIZE:
            window.clear()
            term_num_rows, term_num_cols = tuple(n - 1 for n in window.getmaxyx())
        display_screen(window, regex_to_color, input_file, term_num_rows, term_num_cols)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, args.config)
