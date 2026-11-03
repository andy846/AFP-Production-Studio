"""``python -m afpstudio`` starts the application.

By default it serves the application for a browser. With ``--desktop``
it opens the same application in a native window instead.
"""

from __future__ import annotations

import sys

# The imports are absolute so that this module also works when it is
# frozen by PyInstaller, where a package-relative import has no parent
# package to resolve against.
from afpstudio.app import main
from afpstudio.config import CONFIG


def _run(argv: list[str]) -> int:
    if "--desktop" in argv:
        from afpstudio.desktop import run_desktop

        return run_desktop()
    if CONFIG.mode == "desktop":
        from afpstudio.desktop import run_desktop

        return run_desktop()
    main()
    return 0


if __name__ == "__main__":
    sys.exit(_run(sys.argv[1:]))
