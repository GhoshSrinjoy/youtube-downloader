"""Command-line downloader. Run `python ytdl_cli.py --help` for options."""

import sys

from ytdl.cli import main

if __name__ == '__main__':
    sys.exit(main())
