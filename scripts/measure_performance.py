"""Measure indexing and page-open cost on synthetic workloads.

Usage:
    python scripts/measure_performance.py [pages]

Builds a synthetic AFP with the requested page count, times the
streaming index, and reports peak Python heap usage. The output is plain
text, suitable for pasting into docs/PERFORMANCE.md.
"""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "vendor" / "readafp" / "src"))

from afpstudio.testing import synthetic  # noqa: E402
from afpstudio.viewer.index import build_index  # noqa: E402


def main() -> None:
    page_counts = [int(part) for part in sys.argv[1:] if part.isdigit()] or [
        1000,
        10000,
        20000,
    ]
    work = Path(REPO_ROOT) / ".measure"
    work.mkdir(exist_ok=True)

    print(f"{'pages':>8} {'file_bytes':>12} {'index_ms':>9} {'peak_mib':>9}")
    for pages in page_counts:
        path = work / f"synth-{pages}.afp"
        path.write_bytes(synthetic.simplex_document(pages))
        size = path.stat().st_size

        tracemalloc.start()
        started = time.perf_counter()
        index = build_index(path)
        elapsed_ms = (time.perf_counter() - started) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert index.page_count == pages
        print(
            f"{pages:>8} {size:>12} {elapsed_ms:>9.1f} "
            f"{peak / (1024 * 1024):>9.1f}"
        )
        path.unlink()


if __name__ == "__main__":
    main()
