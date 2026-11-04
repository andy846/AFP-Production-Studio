"""``python -m afpstudio`` starts the application.

By default it serves the application for a browser. With ``--desktop``
it opens the same application in a native window instead.
"""

from __future__ import annotations

import sys
import argparse

# The imports are absolute so that this module also works when it is
# frozen by PyInstaller, where a package-relative import has no parent
# package to resolve against.
from afpstudio.app import main
from afpstudio.config import CONFIG


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="AFP Production Studio")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--desktop", action="store_true")
    modes.add_argument("--web", action="store_true")
    parser.add_argument("file", nargs="?")
    args = parser.parse_args(argv)
    desktop = args.desktop or (not args.web and (CONFIG.mode == "desktop" or bool(args.file)))
    if args.web and args.file:
        parser.error("A startup file requires desktop mode.")
    if desktop:
        from afpstudio.desktop import run_desktop
        return run_desktop(startup_file=args.file)
    main()
    return 0


if __name__ == "__main__":
    sys.exit(_run(sys.argv[1:]))
