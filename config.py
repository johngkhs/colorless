#!/usr/bin/env python

import collections
import curses

regex_to_color = collections.OrderedDict({
    r'(\d a)' : 1,
    r'(\d)' : 2,
})
