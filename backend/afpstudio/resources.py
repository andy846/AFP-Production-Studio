"""Locating bundled resources in a source tree and in a frozen build.

When PyInstaller freezes the application, the package data is unpacked
under ``sys._MEIPASS`` instead of next to the source file. Both the
templates and the sample printer profile live there, so every path
that points at them has to go through this module.
"""

from __future__ import annotations
import sys
from pathlib import Path


def bundle_root() -> Path:
    """The directory that holds the bundled data.

    In a frozen build this is ``sys._MEIPASS``; in a source checkout it
    is the repository root, which holds ``frontend/`` and ``backend/``.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    # backend/afpstudio/resources.py -> backend/afpstudio -> backend -> root
    return Path(__file__).resolve().parents[2]


def frontend_dir() -> Path:
    """The directory holding the Jinja templates and static assets."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "frontend"
    return Path(__file__).resolve().parents[2] / "frontend"


def package_data_dir(package: str) -> Path:
    """The data directory of an installed package.

    ``package`` is the importable package name, for example
    ``"afpstudio.printer_profiles"``. In a frozen build the data sits
    next to the package inside ``sys._MEIPASS``; otherwise it sits
    inside the package directory itself.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / package.replace(".", "/")
    module = sys.modules.get(package)
    if module is not None and getattr(module, "__file__", None):
        return Path(module.__file__).resolve().parent
    # Fall back to the path of this module: the caller is inside the
    # package it is asking about.
    return Path(__file__).resolve().parent
