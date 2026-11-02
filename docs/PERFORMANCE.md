# Performance measurements

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
