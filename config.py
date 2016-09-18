#!/usr/bin/env python

import collections
import curses

regex_to_color = collections.OrderedDict()
regex_to_color['\d aaaaaa'] = 1
regex_to_color['\d'] = 2
regex_to_color['\d a'] = 4
regex_to_color['c.*'] = 8
regex_to_color['200'] = 255
