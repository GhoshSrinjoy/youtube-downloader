"""`python -m ytdl` opens the desktop app; `python -m ytdl.cli` is the command line."""

import sys

from .gui import main

sys.exit(main())
