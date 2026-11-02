# Upstream audit

Both upstream projects were inspected before any code was written. This
page records what was found, because those findings shaped the design.

## Pinned versions

| Project | Repository | Commit | Licence |
|---|---|---|---|
| readAFP | <https://github.com/bone1966/readAFP> | `22938322e4b8bee340d63a8e0a707532c914de53` | MIT |
| Alpheus AFP Parser | <https://github.com/chatelao/alpheusafpparser> | `b2261a78c2254c32ab136c66199a9f86471d79f1` | GPL-3.0-or-later |

Alpheus is **not** published to Maven Central — a request for
`com.github.chatelao:alpheusafpparser` returns 404 — so it is built
from a pinned clone by `scripts/setup_deps.ps1`.

## readAFP

### What it does well

* A complete MO:DCA/PTOCA reader with an SVG renderer that handles text,
  images, barcodes, fonts and overlays.
* The renderer is driven by a whole-document walk: fonts, code pages,
  overlays and named images are declared before the pages that use them,
  so state has to be accumulated across the file.
* A structured field inspector with byte offsets.
* A real test suite: **221 passed, 12 skipped, 3 xfailed** in about
  15 seconds on the vendored copy.

### What had to change

The upstream limits are documented in the code and were confirmed by
reading it:

| Limitation | Where | What Studio does |
|---|---|---|
| The whole file is read into memory before parsing | `readafp/app.py` | `afpstudio.viewer.index` builds a streaming index with `mmap` and keeps only compact per-field arrays |
| Every page is rendered before anything is shown | `readafp/app.py` | `afpstudio.viewer.renderer` renders one page on demand by replaying only the state that page depends on |
| A page count limit caps the rendered output | `MAX_RENDER_PAGES` in `readafp/app.py` | No such limit in the Studio viewer; the cache is bounded by bytes instead |
| The Flask upload limit is small for a print job | upstream configuration | The local-file path is used instead of an upload, and the byte cap is configurable |
| Whole-document parsing is repeated per request | `readafp/app.py` | The index is built once per open document and reused |

### Verification that the lazy renderer is faithful

The lazy renderer was compared against the upstream whole-document
render for **all 178 AFP samples** in the vendored corpus: every page
produced byte-identical SVG, with no mismatches and no errors. That
differential test is part of the Studio test suite, so the two paths
cannot drift apart unnoticed.

## Alpheus AFP Parser

### What it does well

* A complete, structured field, repeating group and triplet
  implementation of MO:DCA, BCOCA, CMOCA, FOCA, GOCA, IOCA and PTOCA.
* Useful as a second opinion: it reports the byte offset of every field
  it decodes, and it keeps the malformed fields rather than throwing
  them away.

### Defects and sharp edges found in the upstream code

These are upstream issues, not Studio issues. They are recorded so the
adapter avoids them.

| Finding | Where | How Studio avoids it |
|---|---|---|
| The PDF path mis-handles font heights | `PdfHandler.java` around line 615 | Studio never calls the PDF handler. The adapter uses `AFPParser` directly and never instantiates `HandlerRegistry` |
| `HandlerRegistry` instantiates `PdfHandlerFactory`, which pulls in iText 9 (AGPL) | `HandlerRegistry` / `PdfHandlerFactory` | The adapter never touches the handler registry, so iText is neither loaded nor distributed |
| `BufferUnderflowException` on parts of the external corpus | Alpheus test suite, `external/` samples | The adapter sets `escalateParsingErrors=false`, records the error and carries on. A partial parse is reported as incomplete, and an incomplete parse can never produce a pass |
| `font_test.afp` triggers a `NullPointerException` | Alpheus test suite | Same handling: the error is captured and reported, not fatal |
| The continuous integration job runs only 13 of the 61 test classes | upstream workflow | Studio runs its own suite against the built adapter jar rather than relying on the upstream job |

## Corpus

The vendored corpus under
`backend/vendor/readafp/testdata/alpheus-corpus/` is GPL-licensed test
material from the Alpheus project and is kept because readAFP's own test
suite depends on it. It is **not** customer data.

Only two files in the corpus carry real media controls:
`alpheus-corpus/external/afplib_start.afp` and `afplib_ende.afp`, both
of which invoke a medium map called `MM000000`. Every other medium
preflight scenario is covered by the synthetic documents built by
`afpstudio.testing.synthetic`, so the suite can state exactly what each
test exercises.

## Structural facts confirmed from the Alpheus source

The MO:DCA byte layouts used by the preflight were confirmed against
the upstream Java classes rather than guessed:

* `MDD_MediumDescriptor` — 13 byte prefix: unit base (1), unit base
  (1), units per unit base (2), units per unit base (2), X extent (3),
  Y extent (3), flag (1), then triplets.
* `MMC_MediumModificationControl` — identifier (1), constant (1), then
  two byte keyword pairs.
* `MCC_MediumCopyCount` — repeating groups of six bytes: start copy (2),
  end copy (2), reserved (1), modification control identifier (1).
* `PGD_PageDescriptor` — unit base (1), unit base (1), units per unit
  base (2), units per unit base (2), X size (3), Y size (3), reserved
  (3), then triplets.
* `AFPUnitBase` — `0x00` units are ten inches, `0x01` units are ten
  centimetres, `0x02` is a logical resolution ratio with no physical
  size.
* Duplex control keyword `0xF4` — `0x01` simplex, `0x02` normal duplex
  (long edge), `0x03` tumble (short edge). A medium map without the
  keyword is simplex.

## Design consequences

1. The Python parser is authoritative for the *viewer* and the
   *preflight*; the Java parser is an optional cross-check. Either can
   be wrong, so a disagreement is a failure, not a warning.
2. Every parser keeps the byte offset of everything it reports, so any
   warning can be traced back to a position in the original file.
3. The preflight never resolves a medium that the AFP does not declare
   and the profile does not map. It says "unresolved" instead.
