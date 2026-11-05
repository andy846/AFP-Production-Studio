# v0.3 Unified Engine implementation record

All development stays on `main`, as requested. This record distinguishes
implemented behavior from later acceptance work; a preview or an operator
attestation is never production evidence.

## Binding decisions

* Keep Python's physical-offset index and readAFP's MIT renderer. Do not
  reconstruct unknown fields or change the stable rendering pipeline.
* Java remains optional. The Windows application must work without Java/Go.
* RDW support is explicit and restricted to unspanned records containing
  exactly one complete 5A-wrapped structured field. Bare-SF RDW, BDW/VBS,
  line data and mixed-mode interpretation are not implemented. Unsupported
  framing fails closed. Java comparison of RDW is explicitly refused.
* Parsed page groups are not verified mailpieces. TLE previews require an
  operator-selected fallback and retain raw bytes and triplet offsets.
* Resource definitions and references are observations. A matching name
  alone does not establish scope. Inventory is a profile-specific operator
  attestation with reference, verifier and date, not actual RIP availability.
* Contract 2.0 JSONL is an outer stream around validated contract 1 metadata.
  Header source SHA-256, ordered field records and a final count/digest are
  mandatory. Missing summaries, overlapping fields and unread input in a
  purportedly complete result are rejected. Legacy contract 1 remains a
  partial comparison for existing development adapters.
* Constrained editing copies unmodified bytes verbatim. It supports known
  IMM names and same-length raw TLE FQN/attribute-value replacements. General
  field serialization, resource renaming and arbitrary merge are separate
  fidelity gates, not implicit capabilities of this writer.

## Verified milestones (2026-11-06)

| Work | Verification |
|---|---|
| RDW open / SVG / IMM timeline / physical offsets | 6 integration cases |
| JSONL summary, checksum, truncation, sequence and caps | unit + real JDK 21 integration |
| Font and named-include dependency observations + inventory validation | unit + API + preflight integration |
| Positioned text with page coordinates and original PTX offset | integration tests; readAFP preview fidelity |
| Bounded worker threads / queue / cooperative deadline cleanup | regressions showing 30 queued tasks use at most 2 workers |
| Byte-identical copy / constrained IMM writer / unsafe outputs | 6 integration cases |
| Combined Studio regression suite at this milestone | **282 passed** |
| Vendored readAFP, upgraded runtime | **224 passed, 11 skipped, 3 xfailed** |
| Python dependency audit after updated pins | no known vulnerabilities reported in resolved `backend/requirements.txt` |

Commands used: `python -m pytest tests -q`,
`python -m pytest backend/vendor/readafp/tests -q` (vendored `src` on
PYTHONPATH), and `python -m pip_audit -r backend/requirements.txt --format json`.
Java tests used a freshly compiled adapter against pinned Alpheus, not a
mock parser. The suite counts above are a milestone, not claims about later
edits or production/RIP fidelity.

## Four-stage UAT scope

| Stage | Integrated functionality | Coverage boundary |
|---|---|---|
| Parser foundation | Explicit standard/RDW, document/group/raw TLE index, resource observations/inventory, contract 2.0 with original offsets and explicit MMC duplex comparison | No BDW/VBS, bare-SF RDW, line/mixed mode, or full independent resource semantics |
| Production analysis | Page-local positioned text, evidence-gated mailpiece candidates, document-local IMM state | Preview text/code-page fallback; named groups alone never establish mailpiece boundaries |
| Engineering | Byte-identical copy, IMM/TLE edits, complete-document extract/merge with provenance | Same-length edits; refuses unproven closure or collisions; no arbitrary rewriting/resource renaming |
| Output and control | Raster PDF/PNG/TIFF preview, bounded batch preflight, persisted evidence journal, same-unit reconciliation and manual sign-off | No RIP/dispatch feed, identity authentication, tamper-proof journal or production-validated conversion |

Use the [Windows UAT guide](V0_3_OPERATING_GUIDE.md) with approved synthetic
or non-customer fixtures. The development build is not approval for a
production job.

## Acceptance evidence

* Studio on portable CPython 3.13.18: **311 passed** with rebuilt optional
  JDK 21 adapter and actual Edge. Browser smoke included open, positioned
  text, engineering copy, page jump, narrow layout and no app requests off loopback.
* Vendored readAFP: **224 passed, 11 skipped, 3 xfailed** with the optional
  fontTools test dependency. The clean 3.13.18 environment without fontTools
  reported **220 passed, 15 skipped, 3 xfailed**. Expected failures are existing
  AFP/PDF reference differences.
* Packaged Windows smoke: native window launched, startup AFP opened 4 pages,
  SVG and IMM sequence verified, CSV/XLSX/PNG written, original SHA-256 unchanged,
  Java disabled. Window close stopped the child and bootloader with no residual
  test extraction folder. Export was checked via the EXE's loopback API.
  Native Browse/Save As click-through was not completed because the computer-use
  approval timed out.
* The independent review caught three fail-open conditions. Added regressions
  verify missing End Document cannot pass, merge cannot borrow another
  document's medium map, and rejects/reprints cannot bypass sheet reconciliation
  by changing units. All four new cases passed in the final suite.
* Reviewed Windows executable: 24,298,880 bytes, 24 licence/notice archive
  entries, zero Java archives. SHA-256:
  `998e479a6f382027fb4fbbeef3115cd646173d2816938b8b6e6b922ddd616741`.
* Clean desktop Python environment: `pip-audit` found no known vulnerabilities;
  `pip check` found no broken requirements. OSV returned no advisories in the
  72 Cargo coordinates from the converter wheel SBOM. This does not cover all
  native OS libraries and is not malware or code-signing attestation.
* CI tests, dependency audit and build workflow is configured in
  `.github/workflows/verify.yml`. There is no remote run of this local change yet.

## Operational limits

* Indexed document/TLE and resource observations have explicit item/byte
  caps. Their `complete` flag describes the supported scan, not full AFP
  semantic coverage.
* Positioned text identifies the PTX structured field, not the exact glyph
  byte. Mapping/font fallback and unsupported drawing behavior can affect
  preview results. Raw code-page declarations remain authoritative evidence.
* Page replay input is capped at 128 MiB; this is not a bound on every image
  decompression allocation inside the upstream renderer.
* Worker deadlines are cooperative, not an OS-enforced Python sandbox.
  Java additionally has its own heap, wall-clock and output limits.
* UI searches use POST bodies and the built-in server omits request URL logs.
  Deployments using their own access logger must redact query strings.
* The development adapter uses a previously built pinned Alpheus library.
  Its Gradle wrapper matched pinned upstream Git blob
  `d3b83982b9b1bccad955349d702be9b884c6e049`. The Gradle 8.5 distribution
  property lacks a pinned distribution SHA-256. Wrapper execution and full
  Java SCA are outside this EXE acceptance; no Java jar is bundled.
* Production acceptance still needs real printer/RIP comparison and operator
  workflow validation; the UAT EXE is unsigned.
