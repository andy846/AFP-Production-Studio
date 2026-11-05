# Security review and remediation summary

This page lists the threats that were considered, what was done about
them, and what is still open. It is written for the people who have to
decide whether to run this on a machine that holds customer documents.

## Threat model

AFP Production Studio runs on a Windows workstation and opens files the
operator chooses. The realistic risks are:

* a hostile or malformed AFP file causing a crash, an unbounded
  allocation, or a path that escapes the working area;
* a document leaving the machine;
* a crafted file name being executed when a report is opened in a
  spreadsheet;
* the report overwriting the very file it describes;
* a browser page on the same machine driving the server.

## What was done

### Input and output safety

| Control | Where |
|---|---|
| Every path from the browser is resolved and checked against an allow-list of directories | `afpstudio/security/paths.py` — `resolve_under_roots` |
| Traversal outside allowed roots, device paths and paths with a null byte are rejected; network files require a configured root or an explicit native selection | `resolve_under_roots`, `FileGrants` |
| An export may not point at the AFP it is describing, even as a hard link | `safe_output_path` |
| Exports are written to a temporary file in the target directory and then renamed | `atomic_write_bytes` |
| The source AFP is opened read-only everywhere | the whole code base; there is no write path for document input |

The allow-list defaults to the operator's Documents, Downloads and
Desktop folders and the working directory. It is overridden with
`AFP_STUDIO_ALLOWED_ROOTS`.

### Parser safety

| Control | Where |
|---|---|
| Structured field lengths and offsets are validated before anything is read | `afpstudio/viewer/index.py` — `build_index` |
| A field that runs past the end of the file stops the scan and is reported | `build_index` records `truncated` and a warning |
| A carriage control byte that is not `0x5A` stops the scan | `build_index` |
| The index holds compact arrays, not the file, so memory does not scale with the file size | `afpstudio/viewer/index.py` |
| Rendered pages are cached against a byte budget, so the cache cannot grow without limit | `afpstudio/viewer/cache.py` |
| Long operations run in a bounded number of worker threads | `afpstudio/viewer/jobs.py` — `JobManager` |
| A parse that did not finish can never produce a pass | `afpstudio/preflight/engine.py` — `INCOMPLETE_PARSE` is a fail |
| A disagreement between the two parsers can never produce a pass | `PARSER_DISAGREEMENT` is a fail |

### Subprocess safety

The Java parser runs as a separate process.

| Control | Where |
|---|---|
| The command is an argument array, never a shell string, and `shell=False` | `afpstudio/parser_adapters/alpheus.py` |
| The process has a wall clock timeout | `AlpheusAdapter.parse` |
| The process has a heap limit (`-Xmx`) | `AlpheusAdapter.command` |
| The output is read with a byte cap | `_read_capped` |
| The process can be cancelled and is killed if it ignores the polite request | `AlpheusAdapter._stop` |
| Standard input is closed, so the adapter cannot block waiting for input | `stdin=subprocess.DEVNULL` |
| No window is shown on Windows | `CREATE_NO_WINDOW` |

### XML

Studio does not parse XML. The XML that Alpheus can produce is not
consumed by this application, so there is no `DocumentBuilderFactory`,
no `TransformerFactory` and no external entity resolution to configure.
This is deliberate: the readAFP vendored tree is used for its parser and
renderer, and those do not read XML either.

### Browser and transport

| Control | Where |
|---|---|
| A strict content security policy with no CDN and no `unsafe-eval` | `afpstudio/security/headers.py` |
| `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cross-Origin-*` and `Permissions-Policy` on every response | `afpstudio/security/headers.py` |
| Every state-changing request needs a CSRF token and a same-origin request | `afpstudio/security/csrf.py` |
| The session cookie is `HttpOnly` and `SameSite=Lax` | `afpstudio/app.py` |
| The session key is generated per install and kept in the data directory | `afpstudio/app.py` — `_secret_key` |
| The server binds to the loopback interface by default | `afpstudio/config.py` |
| The request body is capped | `MAX_CONTENT_LENGTH` |

The one deliberate relaxation exists for readAFP's optional Pyodide
mode, which downloads a WebAssembly engine from a CDN. It is off unless
`AFP_STUDIO_ALLOW_EXTERNAL=1` is set, and it is not used by Studio.

### Privacy

| Control | Where |
|---|---|
| No document content is sent anywhere | there is no outbound request in the code base |
| No telemetry | there is no analytics or crash reporting |
| The session key and the printer profiles live in the local application data folder | `afpstudio/config.py` |
| Temporary files are created under a single root and removed when the job ends | `afpstudio/config.py` — `temp_root` |
| No customer data in the test suite | every AFP used by the tests is synthetic or comes from the publicly licensed corpus |

### Report exports

Every cell written to a CSV or an Excel sheet is passed through
`guard_cell`, which prefixes a value that starts with `=`, `+`, `-` or
`@` with an apostrophe. Without that, a document that contains
`=cmd|...` would execute when an operator opened the export.

## What is still open

* **No authentication.** The server trusts the loopback interface. On a
  shared machine, another local process could reach it. The bind
  address can be changed with `AFP_STUDIO_HOST`, and doing so prints a
  warning.
* **The preflight is not a proof.** It reports what the AFP says and
  what the printer profile maps it to. It cannot tell you what the
  printer actually loaded. Actual media stays "unknown" until a
  production system reports it.
* **Dependency scanning.** The Python dependencies are pinned, but no
  automated vulnerability scan runs as part of the build. The Alpheus
  tree is pinned to a commit; its Gradle wrapper integrity is upstream's
  responsibility, not this project's.
* **The renderer is upstream code.** readAFP's renderer was written for
  a browser UI and has not had a hostile-input fuzzing pass. The
  Studio-specific parsing layers have been checked, but a fuzzing
  programme for the renderer would be worth doing.

## Reporting a problem

Open an issue with the file that triggered it, the Studio version and
the operating system. **Do not attach a customer document**; a
synthetic sample that reproduces the problem is much more useful and
does not put anyone's data in a public tracker.
