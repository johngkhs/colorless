import collections
import curses

regex_to_color = collections.OrderedDict()
regex_to_color['[Ee]rror'] = curses.COLOR_RED
regex_to_color['[Ii]nfo'] = curses.COLOR_BLUE
regex_to_color['[Dd]ebug'] = curses.COLOR_CYAN
regex_to_color['\d{3}'] = 150
