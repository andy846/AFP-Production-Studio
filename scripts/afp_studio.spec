# PyInstaller specification for the AFP Production Studio desktop build.
#
# Build it with:
#     python scripts/build_exe.py
#
# The application is frozen as a single file. The Flask templates, the
# static assets and the sample printer profile are bundled as data and
# are found at run time through afpstudio.resources, which knows about
# sys._MEIPASS.
#
# The spec file runs in a bare namespace, so only the names PyInstaller
# provides (os, SPECPATH, Analysis, ...) may be used here. Everything
# else has to be imported inside the build, not at the top.

# SPECPATH is the directory holding this file, which is scripts/.
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
from PyInstaller.utils.hooks import copy_metadata

readafp_src = os.path.join(REPO_ROOT, "backend", "vendor", "readafp", "src")
readafp_pkg = os.path.join(readafp_src, "readafp")

datas = [
    (os.path.join(REPO_ROOT, "LICENSE"), "licenses/Studio"),
    (os.path.join(REPO_ROOT, "docs", "LICENSE_COMPLIANCE.md"), "licenses/Studio"),
    (os.path.join(REPO_ROOT, "backend", "vendor", "readafp", "LICENSE.MIT"), "licenses/readAFP"),
    (os.path.join(REPO_ROOT, "frontend", "templates"), "frontend/templates"),
    (os.path.join(REPO_ROOT, "frontend", "static"), "frontend/static"),
    (
        os.path.join(
            REPO_ROOT,
            "backend",
            "afpstudio",
            "printer_profiles",
            "profiles",
            "sample_profile.json",
        ),
        "afpstudio/printer_profiles/profiles",
    ),
    # The vendored readAFP foundation ships templates, static assets and
    # sample documents of its own.
    (os.path.join(readafp_pkg, "templates"), "readafp/templates"),
    (os.path.join(readafp_pkg, "static"), "readafp/static"),
    (os.path.join(readafp_pkg, "samples"), "readafp/samples"),
]

for package in ("Flask", "Werkzeug", "Jinja2", "MarkupSafe", "itsdangerous", "click", "blinker",
                "segno", "openpyxl", "et-xmlfile", "Pillow", "resvg-py", "defusedxml", "pywebview"):
    datas += copy_metadata(package, recursive=True)

hiddenimports = [
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
    "readafp",
    "readafp.ptoca",
    "readafp.bcoca",
    "readafp.foca",
    "readafp.gcgid",
    "readafp.triplets",
    "readafp.parser",
    "segno",
    "openpyxl",
]

a = Analysis(
    [os.path.join(REPO_ROOT, "backend", "afpstudio_launcher.py")],
    pathex=[
        os.path.join(REPO_ROOT, "backend"),
        readafp_src,
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "pytest",
        "_pytest",
        "matplotlib",
        "numpy",
        # pywebview can drive a Qt window, but this build uses the
        # Windows WebView2 control. Excluding Qt keeps roughly twenty
        # megabytes of unused runtime out of the executable.
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "PyQt5.QtWebEngine",
        "PyQt5.QtWebEngineWidgets",
    ],
    noarchive=False,
    optimize=0,
)

# pywebview's shared hook collects Android's bridge even on Windows.
# The desktop package uses WebView2 and does not ship any Java archive.
android_bridge = "webview/lib/pywebview-android.jar"
a.datas = [entry for entry in a.datas if entry[0].replace("\\", "/") != android_bridge]
a.binaries = [entry for entry in a.binaries if entry[0].replace("\\", "/") != android_bridge]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AFP-Production-Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

