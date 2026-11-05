# Changelog

All notable changes to this project are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [semantic versioning](https://semver.org/).

## Unreleased

* Open local files and Windows mapped or UNC shares from the native file
  picker or Windows Explorer. File-picker grants apply only to the selected
  document or FormDef and are consumed once.
* Open documents in the viewer workspace; advanced print options remain
  available without switching to a separate opening page.
* Detect common file replacement and metadata changes before serving cached
  results; changed or unavailable sources must be reopened.

## [0.2.2] — 2026-11-05

* Open an AFP file passed to the desktop executable by Windows Explorer.
  The launch grants access only to the selected local file; network
  paths and other files outside the configured roots remain blocked.

## [0.2.1] — 2026-11-04

* Refresh the desktop workspace with a light theme, collapsible panels,
  page navigation, accessible controls and explicit report replacement confirmation.
* Validate profile and final export paths and refuse source-file replacement.
* Track medium-map invokes in a single pass; treat duplicate map names as unresolved.
* Return unknown for sheet estimates with mixed or missing copy counts.
* Clean up document sessions when a job is cancelled after opening.

This is not a production release. Worker-pool limits, in-stage cancellation,
dependency audit, 20,000-page performance and real printer/RIP reconciliation
still require verification. The desktop UI and native dialogs need further
interactive testing on the target company computer.

## [0.2.0] — 2026-11-04

Studio is now a program instead of a server you have to start and a
browser you have to find. The browser mode is still there and still
behaves the same.

### Added

* **A desktop shell** — `pywebview` opens the application in a native
  Windows window. The window hosts the same local server on an
  ephemeral loopback port, so the application logic is unchanged and
  there is only one implementation of every feature.
* **A single executable** — `scripts/build_exe.py` freezes the
  application with PyInstaller into
  `dist/AFP-Production-Studio.exe` (21.7 MiB). The templates, the
  static assets, the sample printer profile and the vendored readAFP
  data are bundled in and located at run time through
  `sys._MEIPASS`. Double-clicking it opens the window; `--web` asks
  for the browser mode instead.
* **Native file dialogs** — **Browse** buttons pick the AFP file and
  an external form definition, and the export button opens a native
  save dialog. The path boxes still accept typed paths, so the
  browser mode is unchanged.
* **A resource locator** — `afpstudio.resources` resolves the
  frontend and the bundled data whether the code runs from the source
  tree or from the frozen executable.
* **`AFP_STUDIO_MODE`** — `desktop` or `web`. The default is still
  `web`, so nothing changes for an existing installation.
* **`scripts/run.ps1 -Desktop`** — creates the environment, installs
  `pywebview` and opens the window.
* **30 new tests** — unit tests for the shell, security tests for the
  bridge and the local server, and end to end tests that start the
  application inside a stand-in for the window toolkit.

### Fixed

* **`segno` is now a declared dependency.** The vendored readAFP
  imports it but it was not in the requirements file, so a fresh
  installation failed at import time with a `ModuleNotFoundError`.
* **The frozen build no longer starts the browser mode.** The
  launcher defaults to `--desktop`, because double-clicking an
  executable has to open the application.

### Security

* The desktop window is served from an ephemeral port on `127.0.0.1`
  only. A second copy of Studio cannot collide with the first, and
  nothing is reachable from the network.
* The native bridge exposes four methods and nothing else: attach,
  pick an AFP file, pick a form definition, save a report. A missing
  window toolkit raises instead of pretending that the user
  cancelled.
* The server is stopped when the window closes, and the temporary
  directory is removed.

### Known limitations

* The executable is not code-signed, so Windows shows the "Windows
  protected your PC" banner on first run.
* The executable does not contain the Java cross-check parser, which
  needs a JDK 21. Point it at a jar with `AFP_STUDIO_ALPHEUS_JAR`.
* `pywebview` is not in the default requirements, because it pulls in
  a .NET dependency that the browser mode does not need.

## [0.1.0] — 2026-11-02

The first public release. It is the working MVP of the production
studio: a local viewer, a structured field inspector, a media
preflight and a production report, built on a vendored copy of
readAFP and an optional cross-check against the Alpheus AFP Parser.

### Added

* **Viewer** — opens an AFP file from disk, indexes it in a single
  streaming pass, renders pages to SVG on demand, and caches them
  within a byte budget. Page navigation, zoom, fit and page search
  are wired to the index rather than to a rendered document.
* **Structure** — every structured field with its byte offset, its
  page and its object name, searchable by name or by text.
* **Media preflight** — walks the MO:DCA media controls of a
  document and reports, for every page, the medium map in force, the
  requested media, the duplex it asks for, the paper stock and tray
  the printer profile maps it to, and a status of *pass*, *warning*,
  *fail* or *unresolved*. Medium map state is carried across pages,
  so an Invoke Medium Map on page three changes what pages three
  and four ask for.
* **Requested / Resolved / Actual** are kept apart. Studio never
  reports actual media; that needs a printer or RIP feed. A missing
  form definition, job ticket or printer mapping produces
  *unresolved*, never a pass.
* **Printer profiles** — configurable media map mappings and
  user-defined rules, with a sample profile installed on first
  start.
* **Resources** — the fonts, code pages and character sets a
  document references but does not embed.
* **Production report** — statistics labelled as parsed, calculated,
  unknown or external, plus a production control report skeleton for
  reconciling the job against the plant. CSV and Excel export.
* **Alpheus adapter** — an optional Java parser driven over a
  versioned JSON contract (`docs/JSON_CONTRACT.md`). It runs as a
  separate process with a heap limit, a wall clock timeout and a
  capped output reader, and Studio keeps working without it.
* **Synthetic test data** — `testdata/sample/demo-medium-transition.afp`,
  a readable four page job with a medium map change on page three.
* **Documentation** — Windows setup, security, licence compliance,
  measured performance, the JSON contract and the upstream findings.

### Security

* The server binds to the loopback interface and sends nothing
  anywhere; there is no telemetry.
* File paths are validated against an allow list of roots, and an
  export can never overwrite the AFP it was produced from.
* The Java adapter is started from an argument array with
  `shell=False`, so a path cannot become a command.
* The viewer is written to be robust against malformed AFP: field
  lengths and offsets are validated, and a parse that does not
  complete fails closed instead of reporting a pass.

### Known limitations

* The preflight covers bracketed pages. Content outside a Begin Page
  / End Page bracket is paginated by the viewer but is not given a
  medium map; the report counts those pages.
* The sheet count is a calculated estimate under a stated duplex
  assumption, not an observed quantity.
* Mailpiece and dispatch counts are never derived from an AFP.
* Text search matches the encoded byte stream, not rendered glyphs.
* The Alpheus cross-check needs a JDK 21 that this repository does
  not ship; `scripts/setup_deps.ps1` builds it when one is present.

[0.2.0]: https://github.com/andy846/AFP-Production-Studio/releases/tag/v0.2.0
[0.1.0]: https://github.com/andy846/AFP-Production-Studio/releases/tag/v0.1.0
