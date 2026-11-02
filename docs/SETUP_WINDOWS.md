# Setting up AFP Production Studio on Windows 11

## What you need

| | |
|---|---|
| Python | 3.11 or newer, from <https://www.python.org/downloads/windows/> |
| A JDK | Only for the optional Java cross-check parser. JDK 21. |
| Disk | About 60 MB for the application, plus the size of the corpus if you want the upstream test suite |

No installer is needed. The application runs from a folder.

## 1. Get the code

    git clone <this repository> AFP-Production-Studio
    cd AFP-Production-Studio

## 2. Create the environment and start it

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

## 3. Open a document

Type the full path of an AFP file into the box on the opening screen,
choose a printer profile if you have one, and press **Open file**.

The path has to be inside an allowed directory. By default that is
Documents, Downloads, Desktop and the folder the server was started in.
To allow another directory:

    $env:AFP_STUDIO_ALLOWED_ROOTS = "D:\Jobs;D:\Archive"

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
| `AFP_STUDIO_HOST` | `127.0.0.1` | The interface to bind to |
| `AFP_STUDIO_PORT` | `8787` | The port |
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
