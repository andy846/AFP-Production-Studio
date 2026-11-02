# Changelog

All notable changes to this project are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [semantic versioning](https://semver.org/).

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

[0.1.0]: https://github.com/andy846/AFP-Production-Studio/releases/tag/v0.1.0
