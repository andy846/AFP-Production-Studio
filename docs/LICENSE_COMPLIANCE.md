# Licence compliance assessment

> **This is an engineering assessment, not legal advice.** It records what
> the licences say, what this project does, and where the obligations are.
> Anyone distributing the software should have a qualified lawyer review
> it for their own situation.

## Summary

| Component | Licence | How it is used | Obligation |
|---|---|---|---|
| readAFP (vendored viewer foundation) | MIT | Vendored source in `backend/vendor/readafp`, with `LICENSE.MIT` and `UPSTREAM_REQUIREMENTS.txt` kept beside it | Keep the copyright notice and licence text in all copies and substantial portions |
| Alpheus AFP Parser | GPL-3.0-or-later | Built from a pinned upstream commit by `scripts/setup_deps.ps1`; the Studio adapter links against it | The combined product must be GPL-3.0-or-later; the corresponding source must be offered with any distribution |
| Studio adapter (`alpheus-adapter/`) | GPL-3.0-or-later | New source in this repository, deliberately linked to Alpheus | Same as Alpheus |
| iText 9 (Alpheus dependency) | AGPL-3.0 | **Not distributed and not required at runtime.** The adapter never loads the PDF handler registry | None, because the code is not shipped. Enabling Alpheus' PDF conversion would create an AGPL obligation and is out of scope |

**The product as a whole is licensed GPL-3.0-or-later.** That is the safe
position, and it is the position the repository is set up for: the
adapter is GPL, the vendored MIT code is compatible with GPL, and no
AGPL-licensed binary is shipped.

## 1. readAFP — MIT

readAFP is the viewer and SVG rendering foundation. It is vendored
verbatim, keeping the upstream layout so that its own test suite runs
unmodified.

MIT requires that the copyright notice and the licence text travel with
the software. Studio therefore keeps:

* `backend/vendor/readafp/LICENSE.MIT` — the upstream licence text,
* `backend/vendor/readafp/UPSTREAM_REQUIREMENTS.txt` — the pinned commit
  and the list of third-party notices, and
* this section, which credits the upstream authors.

MIT code may be combined into a GPL-3.0 work: the MIT terms are
permissive and impose no restriction on the choice of a more restrictive
licence for the combination.

## 2. Alpheus AFP Parser — GPL-3.0-or-later

Alpheus is not vendored into this repository. `scripts/setup_deps.ps1`
clones it at a pinned commit
(`b2261a78c2254c32ab136c66199a9f86471d79f1`), builds the library jar and
packages it together with the Studio adapter into one jar.

The adapter is a new work that is linked to the Alpheus library. Linking
a GPL-3.0 library into a program and shipping them together produces a
combined work that must be distributed under GPL-3.0-or-later.

### Obligations when distributing

1. The whole combined work is offered under GPL-3.0-or-later.
2. The **complete corresponding source** is offered with the
   distribution. In practice that means shipping this repository at the
   released commit, plus the Alpheus checkout at the pinned commit, plus
   the build script that reproduces the adapter jar from them.
3. The Alpheus copyright notices and its licence text are preserved.
   The upstream `LICENSE` file travels with the Alpheus checkout.
4. The Studio adapter's own notices are preserved; it carries the GPL
   header.
5. Any change to the Alpheus sources is stated. The pinned commit is
   recorded in the adapter, in `scripts/setup_deps.ps1` and in
   `docs/UPSTREAM_FINDINGS.md`.

### Internal use

The GPL does not impose distribution obligations on internal use. The
obligations above apply from the moment the combined work is handed to
someone outside the organisation — including a contractor, a client, or
a public repository.

### Process separation is not a loophole

The Studio adapter runs in a separate process and speaks a JSON contract
over a pipe. It is tempting to argue that this makes the two programs
separate works. The Free Software Foundation's GPL FAQ does not support
that argument in general: programs that communicate at a low level, for
example by exchanging data over a pipe or a socket, can still be
considered a single combined work.

**This project does not rely on that argument.** The product is
distributed under GPL-3.0-or-later as a whole, so the question does not
arise. The adapter is kept isolated and replaceable for engineering
reasons — a crash in the Java parser cannot take the server down, and a
second engine can be dropped in — not to escape the licence.

## 3. iText 9 — AGPL-3.0

Alpheus declares `com.itextpdf:kernel`, `io`, `layout` and `barcodes` at
version 9.0.0. iText 9 is licensed under the AGPL-3.0, which is
**not** compatible with GPL-3.0 for a combined distribution.

Studio avoids that problem by not shipping the AGPL code and not
requiring it:

* The adapter jar contains the Alpheus **library** classes, the Studio
  adapter classes and SLF4J. It does not contain any iText class.
* The adapter uses `AFPParser` directly. It never instantiates
  `HandlerRegistry` or `PdfHandlerFactory`, which are the classes that
  pull iText in. Alpheus' own `Afp2Xml` command line tool does use the
  handler registry; the Studio adapter deliberately does not.
* The adapter was verified to run with no iText artifact on the class
  path at all.

If a future version adds PDF conversion through Alpheus, the AGPL
obligations attach to the distributed binary and the distribution model
has to be re-assessed from scratch. That feature is out of scope for
this release.

## 4. Third-party Python dependencies

The Python dependencies are pinned in `backend/requirements.txt`. All of
them (Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click, blinker,
openpyxl, et-xmlfile) are permissively licensed, with one exception to
check before a release: `click` is BSD-3-Clause, and the rest are BSD,
MIT or Apache-2.0. None is a copyleft licence, so none of them changes
the product's licence.

The vendored readAFP tree carries its own third-party notices in
`backend/vendor/readafp/UPSTREAM_REQUIREMENTS.txt`; those must travel
with the vendored source.

## 5. What this means in practice

* **Internal evaluation** — nothing has to be published. Keep the
  upstream notices with the vendored source.
* **Sharing the jar or an installer with a third party** — the whole
  product, including this repository at the matching commit, must be
  offered under GPL-3.0-or-later with the corresponding source.
* **Calling the product "proprietary-licence-safe"** — this assessment
  does **not** support that claim, and the product must not be
  described that way. It is GPL-3.0-or-later because it links a
  GPL-3.0 library.
* **Dropping Alpheus** — the adapter can be omitted. Doing so does not
  change Studio's own GPL-3.0-or-later terms or the vendored MIT notices.
  A different distribution model needs its own rights and licence review.

## 6. Attribution

* readAFP — the upstream authors, MIT licence. See
  `backend/vendor/readafp/LICENSE.MIT`.
* Alpheus AFP Parser — Copyright 2015 Rudolf Fiala, continued by Olivier
  Chatelain, GPL-3.0-or-later. See the `LICENSE` file in the Alpheus
  checkout and the headers in every source file.

## v0.3 preview and dependency notices

* `resvg-py` 0.5.0 uses the MIT licence. Its wheel carries `LICENSE` and
  a CycloneDX SBOM covering embedded Rust components. The spec retains
  package metadata including these files. See [upstream](https://github.com/baseplate-admin/resvg-py).
* Pillow 13.0.0 uses MIT-CMU and ships notices for bundled libraries in its
  wheel licence file. defusedxml 0.7.1 is PSF-2.0. Both are used locally.
* PyInstaller's bootloader exception and pywebview's BSD-3-Clause terms
  apply separately; package metadata and readAFP's MIT notice are retained.
* No Alpheus, iText, Java, Go, afplib, afpbox, xafp or AFPDF binary is part of
  the Windows UAT executable. For optional adapter distribution, review its
  pinned dependency/source obligations separately.
* The [candidate lock](UPSTREAM_COMPATIBILITY_MATRIX.md) records exact
  revisions. No candidate source was copied into the new writer or reader.
* Publish binaries only with the corresponding complete Studio source and
  build instructions at the matching commit. A local UAT build is not a
  licence clearance for third-party binary redistribution.
