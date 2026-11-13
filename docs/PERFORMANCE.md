# Performance measurements

## Page turns with embedded resources

Measured in the packaged Windows WebView2 application on the same local
54.4 MB AFP with 1,138 pages. Times run from the Next/Previous button click
to the new SVG being visible and two animation frames having elapsed.

| Navigation | Previous EXE | Updated EXE |
|---|---:|---:|
| Page 2, first visit | 14.707 s | 0.945 s |
| Page 3, first visit | 15.230 s | 0.315 s |
| Page 4, first visit | 15.217 s | 0.730 s |
| Back to page 3, cached | 0.130 s | 0.158 s |

The displayed SVG hashes matched before and after, the AFP hash was unchanged,
and both EXEs exited and removed their temporary extraction folders. The
uncached renders had been copying the growing payload of unused object
containers at every structured field. The renderer now assembles each container
only if a page references it.

In a separate, paired CPU benchmark of three AFPs with many embedded bitmap
fonts (12, 108 and 116 pages), median render times across three pages per
file went from 0.89–1.27 s to 0.61–0.69 s. The SVGs were byte-identical.
These measurements cover local inputs on one machine. The benchmark files
are excluded from the repository; automated regressions use synthetic or
publicly licensed AFPs.

## v0.3 full-open benchmark: one IMM per page

Measured on Windows build 26300, CPython 3.11.3, synthetic simplex files.
`python scripts/benchmark_v03.py` starts a fresh process per workload, times
indexed open including media analysis/preflight, then renders only the last
page. `tracemalloc` is enabled during timing.

| Framing | Pages / IMM | Bytes | Open s | Last-page render s | Python heap peak MiB | Working-set peak MiB |
|---|---:|---:|---:|---:|---:|---:|
| Standard | 1,000 / 1,000 | 59,197 | 0.390 | 0.005 | 1.88 | 32.94 |
| Standard | 10,000 / 10,000 | 590,197 | 4.009 | 0.055 | 17.76 | 79.79 |
| Standard | 20,000 / 20,000 | 1,180,197 | 9.149 | 0.108 | 35.51 | 132.24 |
| RDW | 1,000 / 1,000 | 75,241 | 0.420 | 0.005 | 1.89 | 33.01 |
| RDW | 10,000 / 10,000 | 750,241 | 4.269 | 0.052 | 17.76 | 79.80 |
| RDW | 20,000 / 20,000 | 1,500,241 | 10.050 | 0.147 | 35.51 | 131.76 |

Every case indexed the expected page/IMM counts, produced the requested
timeline and rendered zero pages during open. Working-set peak includes
the process and preparation; Python heap peak starts after file creation.
These measurements cover small synthetic fields, not large IOCA pages,
network shares, Java comparisons or RIP throughput.

These numbers are from the machine that built the application, using the
synthetic builder in `backend/afpstudio/testing/synthetic.py`. They are
a snapshot, not a guarantee: a different machine, disk or AFP producer
will produce different numbers.

Run the measurement yourself:

    python scripts\measure_performance.py 1000 10000 20000

The script writes a synthetic simplex document, times one streaming
index pass, and reports the peak Python heap allocated during the pass
with `tracemalloc`.

## Streaming index

The index keeps compact `array` objects of offsets, lengths and
identifiers. It never copies the document payload, so its cost grows
with the number of structured fields, not with the bytes in a page.

| Pages | File bytes | Index pass | Peak heap |
|---|---:|---:|---:|
| 1 000 | 59 197 | 149 ms | 0.3 MiB |
| 10 000 | 590 197 | 1 506 ms | 2.4 MiB |
| 20 000 | 1 180 197 | 3 082 ms | 4.7 MiB |

- The synthetic pages carry only the media controls, so the file itself
  is tiny. Real jobs are larger per page; the index pass is linear in
  the number of structured fields, and the mmap means the payload bytes
  are not copied into the Python heap.
- Peak heap is the total allocation during the pass, not the retained
  index. The retained index is smaller still.
- This measures indexing only. Page *rendering* is lazy: opening a
  document renders zero pages, and only the page the operator asks for
  is rendered and cached.

## What is not measured here

- Render time for a single large page containing a complex IOCA image.
  The differential corpus test proves correctness, not speed.
- The Alpheus Java cross-check parser, which runs as a separate process
  with its own `-Xmx` limit.
- Memory of the browser displaying thousands of pages; the API serves
  one page at a time and the cache is byte-budgeted.

Do not treat the figures in this table as a benchmark. They exist so a
claim about large-file behaviour can be checked against the same
procedure.

## Streaming PDF export: Windows 11, Python 3.11.3

Measured with `scripts/benchmark_pdf.py` in separate fresh processes. Each
synthetic AFP page contains two rendered rules and exercises indexed AFP replay,
SVG generation, resvg rasterization, PNG decode and lossless PDF writing. These
measurements ran on a shared development machine alongside other verification.

| Pages | Raster per page at 150 dpi | Export seconds | PDF bytes | Process peak RSS |
|---:|---|---:|---:|---:|
| 1,000 | 60 × 60 (10.16mm square) | 1.6638 | 594,254 | 34.7 MiB |
| 10,000 | 60 × 60 (10.16mm square) | 21.2836 | 6,018,266 | 58.9 MiB |
| 20,000 | 60 × 60 (10.16mm square) | 38.3027 | 12,058,267 | 82.8 MiB |
| 100 | 1,240 × 1,754 (approximately A4) | 23.3805 | 697,843 | 57.5 MiB |

The small-raster runs verify large page counts, page-tree/xref growth and
streaming behavior; they are **not A4 throughput measurements**. The A4 run
contains simple rules rather than production text, images or resource-heavy
pages. None of these timings predicts an arbitrary customer's AFP conversion
speed. AFP index and compact page/reference metadata still grow with page count;
the exporter does not retain every page's raster. RSS includes native allocations,
not just Python heap. The workload samples RSS every 10ms and reports Windows'
process peak working set as well.

Reproduce:

```powershell
python scripts/benchmark_pdf.py
python scripts/benchmark_pdf.py --pages 100 --width-mm 210 --height-mm 297
```

One operator-supplied local 26-page AFP was additionally exported in full at
150 dpi: 26 PDF pages, 4,082,406 bytes, 24.68 seconds and zero rendering warnings.
An independent reader confirmed the page count and the input SHA-256 was
unchanged. This is a local compatibility check, not a production fidelity
certification; the input and resulting PDF are not part of the repository.
