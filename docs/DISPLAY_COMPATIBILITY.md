# AFP preview display compatibility

Studio renders a supported AFP subset. A visible page or a media preflight
Pass is not proof of complete rendering or equivalence to a production RIP.

## Supported paths and checks

| Content | Current coverage |
|---|---|
| PTOCA text | Positioned text, page units, standard colors, quarter-turn rotation, font mappings and rules; remaining controls are reported |
| Fonts | Supported embedded FOCA raster and Type 1/CFF outlines, coded-font sections and code pages; substitution and missing Unicode/glyph coverage are reported |
| IOCA raster | Uncompressed 1/8/24-bit, Group 4, explicitly framed IBM MMR, Group 3 MH/MR, TIFF algorithm 2, PackBits and LZW |
| Image recording | Supported uncompressed bottom-to-top recording and byte bit order; unsupported recording variants are rejected |
| JPEG / CMYK | Embedded JPEG and supported four-plane CMYK JPEG optical preview |
| Object containers | Supported embedded PNG, JPEG and GIF raster payloads |
| Page segments | Embedded raster, text, rules, supported barcodes and vector objects; enclosing BRS names and IPS offsets |
| Overlays | Embedded definitions, enclosing resource names, named/MPO references and scaled composition; nested definitions restore their parent page |
| GOCA | Supported lines, boxes, curves, arcs, filled areas and colors; skipped drawing orders are reported |
| BCOCA | QR preview with supported options; other symbologies are reported, not substituted with an unrelated barcode |
| Missing objects | Missing includes, decode failures, limits and approximations are identified in the Viewer warning panel |

Warnings retain original structured field offsets where available. Resource
warnings follow referenced resources into the page. The SVG also carries the
diagnostics, so a warning is not lost merely because the page was cached.
The Viewer marks recognized omissions as **Incomplete preview** and opens the
issue list automatically. Font or text-mapping approximations have a separate
**Approximate preview** state. **Inspect field** locates the original field,
including document-level resource definitions. Media preflight status is
independent of display completeness.

Diagnostics include truncated PTOCA, GOCA and IOCA data, unknown or flagged
structured fields, incomplete object/resource scopes, failed browser image
decoding and missing shapes. If the diagnostic limit is reached, a further
notice is retained instead of silently dropping all subsequent problems.
An SVG without known issues is labelled internally as not independently
verified, not as production-complete.
These diagnostics describe observed limitations, not an exhaustive validator
for every AFP architecture or every malformed drawing command.

## Measured audit and remaining limits

The bundled publicly licensed corpus contains 178 AFP files. The initial
audit completed parsing and model extraction for 171 files, producing 690
page models; seven files were rejected by the upstream parser before rendering.
No renderer exception occurred for the remaining 171 files. Some corpus entries
are standalone resources or malformed fixtures, not printable documents.
Two barcode-containing files produced no barcode images: the renderer
supports QR, while the corpus also includes unsupported Data Matrix.
The corpus contains only a small subset of image encodings. Additional
codecs are verified with synthetic TIFF-encoded pixel patterns rather than
claimed as tested against arbitrary production AFPs.

All five operator-supplied local AFPs were also indexed (1,400 pages total);
their first, middle and last pages rendered successfully and their source
hashes were unchanged. Three of those files report incomplete glyph-to-text
mapping on sampled pages. This can affect text extraction even where embedded
glyph shapes are visible. These local inputs and their contents are not
included in the repository or automated fixtures.

The automated suite compares lazy and whole-document rendering over the
licensed corpus. This verifies consistency of the two Studio paths; both use
the same renderer and this does not establish independent RIP fidelity.
Synthetic tests exercise image pixel polarity, compression, row order,
dimensions, resource composition, warnings and malformed/unsupported data.

The completeness-diagnostics update was verified locally on Windows with
358 Studio tests passing and 247 vendored readAFP tests passing (11 skipped,
three expected failures). The frozen EXE opened a synthetic AFP directly,
expanded the incomplete-preview panel, showed resource/unsupported/invalid
categories, located the source field and cleared warnings on the next clean
page, with no JavaScript page errors. Closing the window exited the application
and removed its extracted bundle directory. These checks do not establish
production RIP equivalence.

Still requiring further implementation or independent evidence:

- Data Matrix and non-QR barcode symbologies.
- GOCA character strings, markers, inline images, gradients, custom patterns
  and some mixing/character attributes.
- Missing external fonts, overlays and page segments. A resource inventory
  attestation does not supply their binary contents to the renderer.
- Some color spaces, tiling/banding, ABIC, RL4, JBIG2, unframed/dimensionless
  IBM MMR and unsupported image recording variants.
- EPS/PDF and other non-raster object containers, IM1-specific legacy
  structures, line-data/PageDef processing and printer-specific behavior.
- Exact object stacking, suppression/job settings, color management,
  arbitrary rotations and device-dependent font metrics.

The preview warns about recognized omissions. Unknown structured fields and
unsupported architectures can still affect the result; use the Structure
inspector and an independently validated printer/RIP reference for acceptance.
No customer AFP is committed to this repository.

Architecture references: [IOCA Reference](https://afpcinc.org/wp-content/uploads/2016/08/IOCA-Reference-Image-Object-Content-Architecture-Reference.pdf)
and the AFP Consortium references cited in the vendored renderer modules.
