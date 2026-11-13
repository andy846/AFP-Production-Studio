"""Synthetic renderer + streaming PDF benchmark; not production job throughput.

Run with the development Python, e.g.:
    .venv-v03/Scripts/python.exe scripts/benchmark_pdf.py

Each workload runs in a fresh process. RSS includes native renderer/Pillow/zlib
allocations (Windows working set or Linux /proc RSS). No benchmark dependency is
required. AFP indexing/preflight time is reported separately from export time.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/vendor/readafp/src")]


def rss_bytes():
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        get_current = ctypes.windll.kernel32.GetCurrentProcess
        get_current.restype = wintypes.HANDLE
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if get_memory(get_current(), ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize, counters.PeakWorkingSetSize
    elif sys.platform.startswith("linux"):
        try:
            values = {}
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith(("VmRSS:", "VmHWM:")):
                    key, number, _ = line.split()
                    values[key] = int(number) * 1024
            return values["VmRSS:"], values["VmHWM:"]
        except (OSError, KeyError):
            pass
    return None, None


def workload(count, dpi, width_mm, height_mm):
    from afpstudio.document_model.model import DocumentStore
    from afpstudio.engineering.pdf_export import export_pdf
    from afpstudio.testing import synthetic as sf
    from readafp.ptoca import extract_pages
    from readafp.parser import iter_fields
    from readafp.render import page_to_svg

    # A real parsed page with two rules, repeated as independent AFP brackets.
    # It exercises AFP page replay, SVG creation, resvg, PNG decode and PDF writing.
    body = sf.structured_field(0xD3EE9B, b"\x2b\xd3\x06\xe4\x00\x40\x00\x04"
                                      b"\x2b\xd3\x06\xe6\x00\x40\x00\x04")
    bracket = sf.bpg() + sf.pgd(width_mm, height_mm) + body + sf.epg()
    # Reject a benchmark fixture that accidentally measures approved omissions.
    probe, = extract_pages(list(iter_fields(sf.bdt() + bracket + sf.edt())))
    if len(probe.rules) != 2 or 'data-preview-status="incomplete"' in page_to_svg(probe):
        raise ValueError("Benchmark page must contain two rules and no omissions")
    with tempfile.TemporaryDirectory(prefix="afp-pdf-benchmark-") as folder:
        source = Path(folder) / "synthetic.afp"
        with source.open("wb") as output:
            output.write(sf.bdt())
            for _ in range(count):
                output.write(bracket)
            output.write(sf.edt())
        started = perf_counter()
        document = DocumentStore().open(source)
        open_seconds = perf_counter() - started
        descriptor = document.analysis.page_descriptors[0]
        baseline, _ = rss_bytes()
        peak = baseline
        stopped = threading.Event()
        def sample():
            nonlocal peak
            while not stopped.wait(0.01):
                rss, _ = rss_bytes()
                if rss is not None:
                    peak = max(peak or 0, rss)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            result = export_pdf(document, Path(folder) / "preview.pdf", dpi=dpi)
        finally:
            stopped.set()
            sampler.join()
        final_rss, process_peak = rss_bytes()
        if final_rss is not None:
            peak = max(peak or 0, final_rss)
        return {
            "workload": "synthetic AFP page replay + SVG + resvg + PNG decode + lossless PDF",
            "production_speed_claim": False,
            "pages": count, "dpi": dpi,
            "rules_per_page": len(probe.rules),
            "width_mm": descriptor.width_mm, "height_mm": descriptor.height_mm,
            "raster_width": round(descriptor.width_mm / 25.4 * dpi),
            "raster_height": round(descriptor.height_mm / 25.4 * dpi),
            "open_seconds": round(open_seconds, 4),
            "export_seconds": round(result["elapsed_seconds"], 4),
            "output_bytes": result["bytes"], "warning_count": result["warning_count"],
            "rss_before_export_bytes": baseline, "rss_after_export_bytes": final_rss,
            "sampled_export_peak_rss_bytes": peak,
            "process_peak_rss_bytes": process_peak,
            "rss_sample_interval_seconds": 0.01,
            "python": sys.version.split()[0], "platform": sys.platform,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", nargs="+", type=int, default=[1000, 10000, 20000])
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--width-mm", type=float, default=10.16)
    parser.add_argument("--height-mm", type=float, default=10.16)
    parser.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker is not None:
        print(json.dumps(workload(args.worker, args.dpi, args.width_mm, args.height_mm)))
        return
    for count in args.pages:
        if count <= 0:
            parser.error("Page counts must be positive")
        child = subprocess.run([
            sys.executable, str(Path(__file__).resolve()), "--worker", str(count),
            "--dpi", str(args.dpi), "--width-mm", str(args.width_mm),
            "--height-mm", str(args.height_mm)], capture_output=True, text=True, check=True)
        print(child.stdout.strip(), flush=True)


if __name__ == "__main__":
    main()
