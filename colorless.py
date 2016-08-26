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
        return config['regex_to_color']
    else:
        return collections.OrderedDict()

def read_line_with_wrapping(input_file, term_num_cols):
    line = input_file.readline()
    if len(line) > term_num_cols:
        input_file.seek(term_num_cols - len(line), os.SEEK_CUR)
        return line[:term_num_cols]
    return line

def display_screen(window, compiled_regex_to_color, input_file, term_num_rows, term_num_cols):
    window.clear()
    current_position = input_file.tell()
    for i in range(term_num_rows):
        line = read_line_with_wrapping(input_file, term_num_cols)
        window.addstr(i, 0, line)
        for regex, color in compiled_regex_to_color.items():
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
        if read_line_with_wrapping(input_file, term_num_cols) == '':
            break
        term_num_rows -= 1

    if term_num_rows == 0:
        for i in range(1, num_lines + 1):
            if read_line_with_wrapping(input_file, term_num_cols) == '':
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

def main(window, input_file, regex_to_color):
    curses.use_default_colors()
    compiled_regex_to_color = collections.OrderedDict()
    for i, (regex, color) in enumerate(regex_to_color.items(), start=1):
        curses.init_pair(i, color, -1)
        compiled_regex_to_color[re.compile(regex)] = i

    term_num_rows = window.getmaxyx()[0] - 1
    term_num_cols = window.getmaxyx()[1] - 1
    pad = curses.newpad(100, 100)
    # window.scrollok(True)
    # window.setscrreg(0, term_num_rows)
    display_screen(window, compiled_regex_to_color, input_file, term_num_rows, term_num_cols)

    while True:
        user_input = window.getkey()
        if user_input == 'j':
            seek_down(input_file, 1, term_num_rows, term_num_cols)
        elif user_input == 'k':
            seek_up(input_file, 1)
        elif user_input == 'd':
            seek_down(input_file, term_num_rows / 2, term_num_rows, term_num_cols)
        elif user_input == 'u':
            seek_up(input_file, term_num_rows / 2)
        elif user_input == 'f':
            seek_down(input_file, term_num_rows, term_num_rows, term_num_cols)
        elif user_input == 'b':
            seek_up(input_file, term_num_rows)
        elif user_input == 'g':
            input_file.seek(0, os.SEEK_SET)
        elif user_input == 'G':
            input_file.seek(0, os.SEEK_END)
            seek_up(input_file, term_num_rows)
        elif user_input == 'q':
            break
        display_screen(window, compiled_regex_to_color, input_file, term_num_rows, term_num_cols)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    regex_to_color = load_config(args.config)
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, regex_to_color)
