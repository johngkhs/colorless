#!/usr/bin/env python

import collections
import curses

regex_to_color = collections.OrderedDict({
    'Debug' : 3,
    'Error' : curses.COLOR_RED
})
