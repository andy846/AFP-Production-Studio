# AFP Production Studio

A local viewer, structured field inspector and **media preflight** for IBM
AFP print jobs.

The question it exists to answer is the one a print production operator
actually has: *which sheet does each page ask for, and can we prove it?*

Everything runs on the machine it is started on. No document content is
uploaded, there is no telemetry, and the server binds to the loopback
interface.

[![Release v0.2.0](https://img.shields.io/badge/release-v0.2.0-e6a23c)](https://github.com/andy846/AFP-Production-Studio/releases/tag/v0.2.0)
[![Licence: GPL-3.0-or-later](https://img.shields.io/badge/licence-GPL--3.0--or--later-4cc38a)](LICENSE)

The release history is in [`CHANGELOG.md`](CHANGELOG.md).

> **Version 0.2.0 runs as a program.** Since 0.2.0 there is a single
> `AFP-Production-Studio.exe`: double-click it and a window opens. The
> browser mode is still there if you want it.

## What it does

* **Viewer** — opens an AFP file, renders pages as SVG on demand, zooms,
  navigates by page, and shows the media controls of the current page.
* **Structure** — every structured field with its byte offset, its page,
  and its object name, searchable.
* **Media preflight** — per page: the medium map that is in force, the
  sheet it declares, the duplex it asks for, the paper stock and tray
  the printer profile maps it to, and a status of *pass*, *warning*,
  *fail* or *unresolved*.
* **Resources** — the fonts, code pages and character sets the document
  references but does not embed.
* **Production report** — statistics with every number labelled as
  parsed, calculated, unknown or external, plus a production control
  report skeleton for reconciling the job against the plant.
* **Settings** — printer profiles, their media maps and their rules, and
  the status of the optional Java parser.

## The three media states

The preflight never blurs these together:

| State | Meaning | Where it comes from |
|---|---|---|
| **Requested media** | what the AFP asks for | the document and its form definition |
| **Resolved media** | the paper and tray that follow from the operator's printer profile | `printer_profiles/` |
| **Actual media** | what the printer actually loaded | *not produced by Studio* — it needs a printer or RIP feed |

When the form definition, the job ticket or the printer mapping is
missing, the result is **unresolved**, never a pass. A document that
invokes a medium map it does not declare is reported as unresolved, not
as a silent success.

## Getting started

### The executable

Download `AFP-Production-Studio.exe` from the
[releases page](https://github.com/andy846/AFP-Production-Studio/releases)
and double-click it. A window opens. There is no server to start, no
port to remember and no browser tab to find.

Windows shows an "Windows protected your PC" banner, because the
executable is not code-signed. Choose **More info** and **Run anyway**.

Inside the window, **Browse** opens a native file dialog for the AFP
file and for an external form definition, and the export button opens
a native save dialog. The path boxes still accept typed paths.

### From source

    git clone <this repository> AFP-Production-Studio
    cd AFP-Production-Studio
    powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Desktop

opens the same window from a virtual environment. Drop the `-Desktop`
switch and the script serves the application in a browser at
<http://127.0.0.1:8787> instead. Python 3.11 or newer is the only
requirement; a JDK 21 is needed only for the optional Java cross-check
parser.

The full instructions are in
[`docs/SETUP_WINDOWS.md`](docs/SETUP_WINDOWS.md).

### Building the executable

    python scripts\build_exe.py

The result is `dist\AFP-Production-Studio.exe`.

### Trying it without a customer file

The repository ships a synthetic AFP under `testdata/` that exercises a
medium map change halfway through a job. Open it from the opening
screen and the **Media Preflight** tab will show the transition:

    testdata/sample/demo-medium-transition.afp

Never open a customer AFP on a machine that is not allowed to hold it.
Studio does not send anything anywhere, but the file is read from disk
and rendered locally.

## How it is put together

    AFP-Production-Studio/
    ├── backend/
    │   ├── afpstudio/
    │   │   ├── api/               the local HTTP API
    │   │   ├── desktop.py         the native window shell
    │   │   ├── document_model/    the unified document model
    │   │   ├── parser_adapters/   the versioned JSON contract and the Java runner
    │   │   ├── preflight/         the MO:DCA media walk and the preflight engine
    │   │   ├── printer_profiles/  the profile schema, the rules and the sample
    │   │   ├── reports/           statistics, exports and the control report
    │   │   ├── resources.py       where the templates live, source tree or frozen
    │   │   ├── security/          paths, headers, CSRF
    │   │   ├── testing/           synthetic AFP builders
    │   │   └── viewer/            the streaming index, the lazy renderer, the cache
    │   ├── afpstudio_launcher.py  the entry point of the frozen build
    │   └── vendor/readafp/        the MIT licensed viewer foundation, pinned
    ├── frontend/                  Jinja templates and vanilla JavaScript, no CDN
    ├── alpheus-adapter/           the GPL Java adapter that fronts Alpheus
    ├── tests/                     unit, integration, security and end to end
    ├── docs/                      licence, security, contract and upstream notes
    └── scripts/                   setup, launch and the PyInstaller build

Two design decisions shape everything else:

1. **The index is streaming.** `afpstudio.viewer.index` makes one pass
   over the file with `mmap` and keeps compact arrays of offsets,
   lengths and identifiers. A 20 000 page job costs a few megabytes of
   index, not the size of the file.
2. **Pages are rendered on demand.** `afpstudio.viewer.renderer`
   replays only the state a page depends on. The result was checked
   against the upstream whole-document render for all 178 corpus
   samples, byte for byte.
3. **The window and the browser are the same application.**
   `afpstudio.desktop` runs the same server on an ephemeral loopback
   port inside a native window. The executable is that, frozen.

## Testing

    python -m pytest tests -q

The suite covers the media preflight (synthetic simplex, duplex,
multi-map, IMM transition, missing form definition, unknown media,
truncated file, invalid structured field length, missing resource and
conflicting profile rules), the security controls, the report exports
and the end to end API.

The upstream readAFP suite is the regression check for the viewer and
runs unmodified:

    python -m pytest backend/vendor/readafp/tests -q

Only synthetic documents and the publicly licensed corpus are used. No
customer AFP ever enters the test suite.

## Licence

**GPL-3.0-or-later**, because it links against the GPL-3.0-or-later
Alpheus AFP Parser. The vendored readAFP foundation is MIT licensed and
keeps its notices. No AGPL-licensed iText code is distributed or
required at runtime.

The full assessment is in
[`docs/LICENSE_COMPLIANCE.md`](docs/LICENSE_COMPLIANCE.md). It is an
engineering assessment, not legal advice.

## Known limitations

* The preflight covers **bracketed** pages. Content that is not inside
  a Begin Page / End Page bracket is paginated by the viewer but is not
  given a medium map; the report says how many such pages there are.
* The sheet count is a *calculated* estimate under a stated assumption
  about duplex pairing. It is not an observed quantity.
* Mailpiece and dispatch counts are never derived from an AFP. They stay
  unknown until a production system reports them.
* The text search finds the query in the **encoded** data stream. It is
  a byte search, not a glyph search, and the interface says so.
* No performance target is claimed without a measurement. See
  `docs/PERFORMANCE.md`.
