#!/usr/bin/env python

import collections
import curses

regex_to_color = collections.OrderedDict({
    r'(\d aaaaaa)' : 1,
    r'(\d)' : 2,
    r'(\d a)' : 4,
})
