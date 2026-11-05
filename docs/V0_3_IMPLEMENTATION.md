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

## Remaining acceptance gates

Integrated engineering UI, larger-workload benchmark, browser/EXE checks,
CI, packaging notices and final complete regression results are recorded
as they run. Full AFP editing/merge/split, conversion, verified mailpiece
profiles and persisted production evidence require their own bounded
implementations and tests; none may be advertised solely from a scaffold.

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
* UI searches use POST bodies. Legacy GET search remains for compatibility;
  deployments using their own access logger must redact query strings.
