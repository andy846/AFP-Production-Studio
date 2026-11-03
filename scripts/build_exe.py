"""Build the single-file Windows executable.

    python scripts/build_exe.py

The result is ``dist/AFP-Production-Studio.exe``. It needs the same
Windows runtime that the browser build needs, plus the WebView2
runtime, which Windows 11 ships with.

The build is reproducible: the dependency versions come from
``backend/requirements.txt`` and the frozen data is listed in
``scripts/afp_studio.spec``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = Path(__file__).resolve().parent / "afp_studio.spec"
ENTRY = REPO_ROOT / "backend" / "afpstudio" / "__main__.py"

#: The packages the frozen application needs at run time.
RUNTIME_PACKAGES = [
    "Flask==2.3.3",
    "Werkzeug==2.3.7",
    "Jinja2==3.1.4",
    "MarkupSafe==2.1.5",
    "itsdangerous==2.2.0",
    "click==8.1.7",
    "blinker==1.8.2",
    "segno==1.6.6",
    "openpyxl==3.1.5",
    "et-xmlfile==2.0.0",
    "pywebview==6.2.1",
    "pyinstaller==6.12.0",
]


def _run(command: list[str]) -> None:
    print(" ".join(command), flush=True)
    result = subprocess.run(command, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def ensure_dependencies() -> None:
    """Install the packages the frozen build needs."""
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            *RUNTIME_PACKAGES,
        ]
    )


def build() -> Path:
    """Freeze the application and return the path of the executable."""
    for folder in ("build", "dist"):
        target = REPO_ROOT / folder
        if target.exists():
            for item in target.iterdir():
                if item.is_dir():
                    import shutil

                    shutil.rmtree(item)
                else:
                    item.unlink()

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(SPEC),
        ]
    )

    exe = REPO_ROOT / "dist" / "AFP-Production-Studio.exe"
    if not exe.is_file():
        raise SystemExit(f"The build did not produce {exe}.")
    return exe


def main() -> int:
    if not ENTRY.is_file():
        raise SystemExit(f"Cannot find the entry point at {ENTRY}.")
    if os.name != "nt":
        print(
            "This build target is Windows. The application still runs "
            "elsewhere in browser mode."
        )
        return 1
    ensure_dependencies()
    exe = build()
    print("")
    print(f"Built {exe}")
    print(f"Size: {exe.stat().st_size / (1024 * 1024):.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
