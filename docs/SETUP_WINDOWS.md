# Setting up AFP Production Studio on Windows 11

There are three ways to run it, in the order you are likely to want
them:

1. **The single executable** - double-click it, a window opens. No
   Python, no server, no browser.
2. **From source, in a desktop window** - the same window, driven by
   the Python in your virtual environment. This is the development
   path.
3. **From source, in a browser** - the original HTTP mode, still
   supported and still useful for remote debugging.

The desktop shell and the browser mode are the *same* application.
The window hosts the same local server; only the presentation differs.

## What you need

| | |
|---|---|
| For the executable | Nothing beyond Windows 11. The WebView2 runtime ships with it. |
| For running from source | Python 3.11 or newer, from <https://www.python.org/downloads/windows/> |
| A JDK | Only for the optional Java cross-check parser. JDK 21. |
| Disk | About 60 MB for the application, plus the size of the corpus if you want the upstream test suite |

## 1. The executable

Download `AFP-Production-Studio.exe` from the
[releases page](https://github.com/andy846/AFP-Production-Studio/releases)
and double-click it.

The window opens on the viewer workspace. Use **Browse** next to the path
box to pick the AFP file, and **Browse** next to the form definition
box to pick an external FormDef. The export button opens a normal save
dialog. The path boxes still work, so a file can also be typed in.

Windows may show a "Windows protected your PC" banner, because the
executable is not code-signed. Choose **More info** and **Run anyway**.

The executable starts a local server on `127.0.0.1` on an ephemeral
port - the port is picked by the operating system so that two copies
of Studio never collide. Nothing is reachable from the network and
nothing is sent anywhere. When you close the window the server stops
and the temporary directory it used is removed.

To run the executable in browser mode instead:

    AFP-Production-Studio.exe --web

It then serves the application on <http://127.0.0.1:8787>.

## 2. From source, in a desktop window

    git clone <this repository> AFP-Production-Studio
    cd AFP-Production-Studio
    powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Desktop

The script creates `.venv`, installs the pinned dependencies, installs
`pywebview` and opens the window. The same thing by hand:

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
    .\.venv\Scripts\python.exe -m pip install pywebview==6.2.1
    $env:PYTHONPATH = "$PWD\backend;$PWD\backend\vendor\readafp\src"
    .\.venv\Scripts\python.exe -m afpstudio --desktop

`pywebview` is deliberately **not** in `backend/requirements.txt`. It
drives the window through .NET, and the browser mode does not need it,
so the default install stays small and free of that dependency. It
needs .NET Framework 4.8 (present on Windows 11) and the WebView2
runtime (also present on Windows 11).

## 3. From source, in a browser

    powershell -ExecutionPolicy Bypass -File scripts\run.ps1

The script creates `.venv`, installs the pinned dependencies and starts
the server. When it prints the address, open it:

    http://127.0.0.1:8787

To use a different port:

    powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Port 9000

### Starting it by hand

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
    $env:PYTHONPATH = "$PWD\backend;$PWD\backend\vendor\readafp\src"
    .\.venv\Scripts\python.exe -m afpstudio

## 4. Open a document

Type the full path of an AFP file into the box on the opening screen,
choose a printer profile if you have one, and press **Open file**. In
desktop mode the **Browse** button opens a file dialog instead, which
is the easier way.

The path has to be inside an allowed directory. By default that is
Documents, Downloads, Desktop and the folder the server was started in.
To allow another directory:

    $env:AFP_STUDIO_ALLOWED_ROOTS = "D:\Jobs;D:\Archive"

The source AFP is only ever opened for reading. An export can never
overwrite it.

### Open AFP files from Windows Explorer

With a build that supports file association, right-click an `.afp` file,
choose **Open with**, then **Choose another app**. Browse to
`AFP-Production-Studio.exe` and select **Always**. Windows controls the
default application; Studio does not change it automatically. After
that, double-clicking an AFP file starts the desktop viewer and opens
the selected file. Move the executable to its permanent location before
setting the association.

For a local or network file outside configured roots, this launch grants
access only to the selected file, not its containing folder. Native Browse
grants a single-use permission to its selected document or FormDef. Manually
entered paths must be inside `AFP_STUDIO_ALLOWED_ROOTS`. Windows drive-letter
shares and UNC paths such as `\\server\share\job.afp` are supported; Windows
controls the credentials. Studio does not store share passwords.

If a source file becomes unavailable or its size, timestamps or identity
change, Studio invalidates the document and asks you to reopen it. Metadata
checks cannot detect every possible change on all network filesystems: do not
edit or replace a file while it is being processed. Browser polling is periodic,
so an already displayed result may remain visible briefly after a change.

## Printer profiles

Printer profiles live in
`%LOCALAPPDATA%\AFP-Production-Studio\printer_profiles`. A sample is
installed the first time Studio starts. The format is documented in
`backend/afpstudio/printer_profiles/profiles/sample_profile.json`.

The Settings tab lists the profiles that were found and shows the
active one.

## The optional Java cross-check

Studio parses AFP on its own. The Alpheus parser is a second,
independent engine used to cross-check the media analysis when you ask
for it on the opening screen.

    powershell -ExecutionPolicy Bypass -File scripts\setup_deps.ps1
    $env:AFP_STUDIO_ALPHEUS_JAR = "$PWD\alpheus-adapter\build\alpheus-adapter.jar"

The script clones Alpheus at a pinned commit, builds it and packages
the adapter. It needs network access the first time; after that it
works from the Gradle cache.

If the jar is missing, Studio says so in the Settings tab and carries
on with its own parser. Nothing else changes.

The frozen executable does **not** include the Java parser - it needs
a JDK 21 that cannot be shipped inside the executable. Point the
executable at a jar on the machine with:

    $env:AFP_STUDIO_ALPHEUS_JAR = "D:\Tools\alpheus-adapter.jar"

## Building the executable yourself

    python scripts\build_exe.py

The result is `dist\AFP-Production-Studio.exe`. The script installs the
pinned runtime packages into the current interpreter and then freezes
the application with PyInstaller. The templates, the static assets,
the sample printer profile and the vendored readAFP data are bundled
into the executable; `backend/afpstudio/resources.py` finds them at run
time through `sys._MEIPASS`.

The build takes a few minutes and needs the PyInstaller wheel. It is
reproducible: the dependency versions come from
`backend/requirements.txt` and the frozen data is listed in
`scripts/afp_studio.spec`.

The executable is not code-signed, so Windows shows the "Windows
protected your PC" banner on first run.

## Running the tests

    .\.venv\Scripts\python.exe -m pytest tests -q

The upstream readAFP suite runs as well, and is the regression check
for the viewer:

    $env:PYTHONPATH = "$PWD\backend\vendor\readafp\src"
    .\.venv\Scripts\python.exe -m pytest backend\vendor\readafp\tests -q

## Where things are stored

| | |
|---|---|
| Printer profiles | `%LOCALAPPDATA%\AFP-Production-Studio\printer_profiles` |
| Session key | `%LOCALAPPDATA%\AFP-Production-Studio\session.key` |
| Temporary files | `%TEMP%\afp-production-studio` |

Nothing else is written, and nothing is sent anywhere. The source AFP
is only ever opened for reading.

## Configuration

Every setting has an environment variable. The defaults are for a
single operator on a single machine.

| Variable | Default | Meaning |
|---|---|---|
| `AFP_STUDIO_MODE` | `web` | `desktop` opens the window; `web` serves the browser mode |
| `AFP_STUDIO_HOST` | `127.0.0.1` | The interface to bind to |
| `AFP_STUDIO_PORT` | `8787` | The port. Ignored in desktop mode, which takes an ephemeral port |
| `AFP_STUDIO_ALLOWED_ROOTS` | Documents, Downloads, Desktop, cwd | Where AFP files may be read from, separated by `;` |
| `AFP_STUDIO_MAX_LOCAL_FILE_BYTES` | 8 GiB | The largest file that may be opened |
| `AFP_STUDIO_PAGE_CACHE_BYTES` | 64 MiB | The rendered page cache budget |
| `AFP_STUDIO_MAX_JOBS` | 2 | Concurrent background jobs |
| `AFP_STUDIO_DATA_DIR` | `%LOCALAPPDATA%\AFP-Production-Studio` | Where profiles and the session key live |
| `AFP_STUDIO_ALPHEUS_JAR` | unset | The Java adapter jar |
| `AFP_STUDIO_JAVA` | `java` | The Java executable |
| `AFP_STUDIO_ADAPTER_TIMEOUT` | 180 | Seconds before the Java parser is killed |
| `AFP_STUDIO_ADAPTER_HEAP` | `1024m` | The Java parser's heap limit |
| `AFP_STUDIO_ALLOW_EXTERNAL` | unset | Leave unset. It relaxes the content security policy for readAFP's Pyodide mode, which downloads from a CDN |

## Troubleshooting

**The window opens blank.** The most common cause is a content
security policy that blocks the local bootstrap script. Check that no
proxy is rewriting `127.0.0.1`, and that the antivirus is not
intercepting loopback traffic.

**"pywebview is not installed."** Run the install from section 2 by
hand. `scripts\run.ps1 -Desktop` does it for you.

**The window opens and closes immediately.** The usual reason is a
missing WebView2 runtime. Install the *Evergreen* runtime from
<https://developer.microsoft.com/microsoft-edge/webview2/>.

**The executable will not start on another machine.** It needs the
WebView2 runtime and the .NET Framework 4.8. Both are present on a
current Windows 11 installation.
