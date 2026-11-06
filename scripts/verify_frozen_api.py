"""Smoke an already started frozen desktop instance using its local HTTP API.

The input must be a disposable synthetic AFP. Start the EXE with the file as
its startup argument and an isolated data directory before running this.
"""
import argparse
import hashlib
import http.cookiejar
import json
from pathlib import Path
import time
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("synthetic_source", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    if urlparse(args.url).hostname != "127.0.0.1":
        parser.error("Only the loopback smoke-test instance is allowed.")
    origin = args.url.rstrip("/")
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    source = args.synthetic_source.resolve(strict=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = args.output_directory.resolve(strict=True)
    token = None

    def request(path, body=None, raw=False):
        headers = {"Content-Type": "application/json", "X-CSRF-Token": token} if body is not None else {}
        with opener.open(Request(origin + path, json.dumps(body).encode() if body is not None else None, headers), timeout=15) as response:
            data = response.read()
            return data if raw else json.loads(data)

    token = request("/api/csrf")["token"]
    documents = request("/api/documents")["documents"]
    document = next(item for item in documents if Path(item["path"]).resolve() == source)
    assert document["page_count"] == 4
    assert request("/api/parser")["available"] is False
    endpoint = "/api/documents/" + document["document_id"]
    assert b"<svg" in request(endpoint + "/page/0.svg", raw=True)
    assert b"Page 4 of 4" in request(endpoint + "/page/3.svg", raw=True)
    preflight = request(endpoint + "/preflight")
    assert [page["medium_map"] for page in preflight["pages"]] == ["MAP01", "MAP01", "MAP02", "MAP02"]
    assert preflight["status"] == "unresolved"
    for fmt in ("csv", "xlsx"):
        target = output / ("frozen-report." + fmt)
        result = request(endpoint + "/export", {"format": fmt, "path": str(target)})
        assert result["bytes"] == target.stat().st_size
        assert target.read_bytes().startswith(b"PK" if fmt == "xlsx" else b"\xef\xbb\xbf")
    png = output / "frozen-preview.png"
    job = request(endpoint + "/engineering", {"operation": "png", "path": str(png), "pages": [0]})["job"]
    for _ in range(100):
        job = request("/api/jobs/" + job["id"])
        if job["state"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(.1)
    assert job["state"] == "completed", job
    assert png.read_bytes().startswith(b"\x89PNG")
    def completed(job):
        for _ in range(100):
            job = request("/api/jobs/" + job["id"])
            if job["state"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(.1)
        assert job["state"] == "completed", job
        return job["result"]

    copy = output / "comparison-source.afp"
    copy.write_bytes(source.read_bytes())
    other = completed(request("/api/documents/open", {"path": str(copy)})["job"])
    comparison = completed(request("/api/comparisons", {
        "left": document["document_id"], "right": other["document_id"],
    })["job"])
    comp_endpoint = "/api/comparisons/" + comparison["comparison_id"]
    paired = request(comp_endpoint)
    assert [(row["left"], row["right"]) for row in paired["items"]] == [(i, i) for i in range(1, 5)]
    diff = completed(request(comp_endpoint + "/visual", {"row": 0, "revision": paired["revision"]})["job"])
    assert diff["changed_pixels"] == 0
    assert request(comp_endpoint + "/visual/1/0.png", raw=True).startswith(b"\x89PNG")
    report = output / "frozen-comparison.xlsx"
    request(comp_endpoint + "/export", {"format": "xlsx", "path": str(report), "revision": 1})
    assert report.read_bytes().startswith(b"PK")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    print(json.dumps({"frozen_startup_open": True, "pages": 4, "svg": True, "imm_timeline": True,
        "unresolved_without_profile": True, "java_disabled": True, "csv": True, "xlsx": True,
        "png": True, "source_unchanged": True, "comparison": True,
        "comparison_overlay": True, "comparison_xlsx": True}))


if __name__ == "__main__":
    main()
