# Workflow and PDF upgrade verification

Local Windows verification, 2026-11-13, Python 3.11.3, Studio 0.3.1 development
build. These checks do not constitute a production RIP fidelity approval.

## Executed checks

| Check | Actual result |
|---|---|
| Full Studio suite | 471 passed in 149.63 seconds |
| Vendored readAFP suite | 247 passed, 11 skipped, 3 expected failures |
| PDF engine tests | 90 passed; independent pypdf and MuPDF verification |
| Async controller regression tests | Three races reproduced with earlier handlers, all three passed after fixes |
| Packaged Windows EXE | Built successfully; real native Save As selected a new synthetic output |
| Native PDF export | 24 synthetic A4 pages exported and independently reopened; 225,523 bytes |
| Native UI | Dark theme, 800px side drawer and source startup verified; zero JavaScript page errors |
| Windows icons | EXE and live 32px/64px window icons matched the artwork |
| Native shutdown | Test application exited and its extracted bundle directory was removed |
| Local operator AFP | All 26 pages exported, independent page count confirmed, zero rendering warnings, original SHA-256 unchanged |

An additional Open PDF action returned HTTP 200 and the generated one-page
synthetic PDF appeared in the configured external reader's window title. That
extra probe hit a Playwright event-loop error during automation teardown; it
is not counted as a clean automated test pass. Studio still shut down cleanly.
Inspection inside the external reader was not performed because that separate
app's computer-use approval timed out; the generated PDF was independently read
by the test reader instead.

The full suite includes export selection/ordering, mixed page dimensions,
lossless color pixels, white pages, source modification/closure, cancellation,
overwrite protection, invalid SVG, malformed diagnostics and rollback. Existing
viewer, parser, preflight, reports, comparison and source-protection checks ran.
The benchmark workload and measured limitations are in [PERFORMANCE.md](PERFORMANCE.md).

Skipped tests require upstream samples/corpus absent from this checkout;
the expected failures remain the documented FOP AFP/PDF pagination/font
differences. They are not newly passing cases.

## Review fixes

- Known invalid/truncated page data fails even when omission approval is supplied.
  Missing resources and supported diagnostic classes for unsupported content still
  require explicit approval. Unknown geometry and failed raster decoding are fatal.
- Cancellation requested before the submission response is retained and sent when
  the job ID arrives. Replies from an older cancellation cannot alter a new task.
- Document ID, page and export settings are captured together before a native
  Save As await. Retries discard the previous job ID.
- Final publication checks source registration while holding the store lock,
  without reacquiring that lock from the writer's cancellation callback.
- The theme button indicates saving and disables briefly, avoiding ignored
  rapid clicks while its preferences request is pending.

## Packaging and remaining limits

The PDF writer adds no runtime PDF dependency. PyInstaller archive inspection
confirmed test-only `fitz`, `pymupdf`, `pypdf` and `psutil` modules are absent.
The CI workflow installs the independent readers for testing only; the recorded
test results above are local and do not represent a remote GitHub Actions run.

PDF is raster preview fidelity, without selectable text, PDF/A/PDF/X conformance
or printer/RIP equivalence. Existing AFP rendering limitations still apply. A
known-size descriptor is required; implicit/unbracketed pages without reliable
physical dimensions are refused. Native rendering cannot be interrupted inside
one native call; cancellation is checked at the surrounding page boundaries.
Manually entered export destinations retain the configured permitted roots.
The current GOCA decoder uses a shared diagnostic code: known corruption messages
are matched explicitly and unsupported orders remain separately approvable.

No customer AFP or resulting customer PDF is committed. Local test artifacts
and the JUnit report remain in the ignored `.measure` directory.
