"""The entry point of the frozen desktop build.

PyInstaller runs a script, not a package, so the package has to be put
on ``sys.path`` before it can be imported. This module is what the
built executable starts.
"""

from __future__ import annotations

import os
import sys


def _prepare() -> None:
    """Make the application packages importable."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [here, os.path.join(here, "backend")]
    for candidate in candidates:
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)


def main() -> int:
    _prepare()
    from afpstudio.__main__ import _run

    argv = sys.argv[1:]
    # The frozen build is the desktop program: double-clicking it must
    # open the window, not serve a page for a browser that is not
    # there. "--web" asks for the browser mode instead.
    if not argv:
        argv = ["--desktop"]
    elif argv[0] == "--web":
        argv = []

    return _run(argv)


if __name__ == "__main__":
    sys.exit(main())
