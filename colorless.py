#!/usr/bin/env python

import argparse
import collections
import curses
import os
import re

def load_config(config_filepath):
    if config_filepath:
        config = {}
        execfile(config_filepath, config)
        return config['regex_to_color']
    else:
        return collections.OrderedDict()

def display_screen(window, regex_to_color, input_file, num_lines):
    window.clear()
    current_position = input_file.tell()
    for i in range(num_lines):
        line = input_file.readline()
        window.addstr(i, 0, line)
        for regex, color in regex_to_color.items():
            tokens = re.split(regex, line)
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

def seek_down(input_file, num_lines):
    END_OF_FILE = ''
    while num_lines > 0:
        char = input_file.read(1)
        if char == END_OF_FILE:
            return
        elif char == '\n':
            num_lines -= 1

def main(window, input_file, regex_to_color):
    curses.use_default_colors()
    num_lines = window.getmaxyx()[0] - 1
    # window.scrollok(True)
    # window.setscrreg(0, num_lines)
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLUE)
    display_screen(window, regex_to_color, input_file, num_lines)

    while True:
        user_input = window.getkey()
        if user_input == 'j':
            seek_down(input_file, 1)
        elif user_input == 'k':
            seek_up(input_file, 1)
        elif user_input == 'd':
            seek_down(input_file, num_lines / 2)
        elif user_input == 'u':
            seek_up(input_file, num_lines / 2)
        elif user_input == 'f':
            seek_down(input_file, num_lines)
        elif user_input == 'b':
            seek_up(input_file, num_lines)
        elif user_input == 'q':
            break
        display_screen(window, regex_to_color, input_file, num_lines)

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='A less-like pager utility with regex highlighting capabilities')
    arg_parser.add_argument('-c', '--config', metavar='config.py', nargs='?')
    arg_parser.add_argument('filepath')
    args = arg_parser.parse_args()
    regex_to_color = load_config(args.config)
    with open(args.filepath, 'r') as input_file:
        curses.wrapper(main, input_file, regex_to_color)
