"""Desktop app. Run `python ytdl_gui.py` (this is also the entry point of the packaged builds)."""

import sys

from ytdl.gui import main

if __name__ == '__main__':
    sys.exit(main())
