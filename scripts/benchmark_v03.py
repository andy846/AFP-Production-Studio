"""Isolated synthetic 1k/10k/20k-page + one-IMM-per-page acceptance benchmark."""
import argparse
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/vendor/readafp/src")]


def peak_working_set():
    if os.name != "nt":
        return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.PeakWorkingSetSize / 1024**2


def worker(pages, framing):
    from afpstudio.testing import synthetic
    from afpstudio.document_model.records import iter_records
    from afpstudio.document_model.model import DocumentStore
    raw = synthetic.simplex_document(pages)
    if framing == "rdw":
        raw = b"".join((len(r.payload) + 4).to_bytes(2, "big") + b"\0\0" + r.payload
                       for r in iter_records(io.BytesIO(raw), "standard"))
    with tempfile.TemporaryDirectory(prefix="afp-v03-bench-") as directory:
        path = Path(directory) / "synthetic.afp"
        path.write_bytes(raw)
        size = len(raw)
        del raw
        store = DocumentStore()
        tracemalloc.start()
        started = time.perf_counter()
        document = store.open(path, input_format=framing)
        open_seconds = time.perf_counter() - started
        assert document.indexed_page_count == pages and len(document.analysis.invokes) == pages
        assert all(p.medium_map == "MAP01" for p in document.preflight.pages)
        assert document.renderer._extra == [], "Opening must not render bracketed pages"
        started = time.perf_counter()
        svg = document.page_svg(pages - 1)
        assert "<svg" in svg
        render_seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {"pages": pages, "imm_records": len(document.analysis.invokes), "framing": framing,
                "file_bytes": size, "open_seconds": round(open_seconds, 3),
                "last_page_seconds": round(render_seconds, 3), "python_peak_mib": round(peak / 1024**2, 2),
                "process_peak_working_set_mib": peak_working_set(), "pre_rendered_pages": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=int)
    parser.add_argument("--format", default="standard", choices=("standard", "rdw"))
    parser.add_argument("--output", default=".measure/v03-benchmark.json")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.format)))
        return
    results = []
    for framing in ("standard", "rdw"):
        for pages in (1000, 10000, 20000):
            run = subprocess.run([sys.executable, __file__, "--worker", str(pages), "--format", framing],
                                 capture_output=True, text=True, check=True, timeout=300)
            result = json.loads(run.stdout)
            results.append(result)
            print(json.dumps(result), flush=True)
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"platform": platform.platform(), "python": platform.python_version(), "results": results}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
