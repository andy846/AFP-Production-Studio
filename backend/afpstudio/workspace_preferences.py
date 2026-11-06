"""Bounded, local workspace preferences; document contents are never saved."""
import json
import math
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

from .security.paths import atomic_write_bytes

THEMES = ("light", "dark")


class WorkspacePreferences:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def _reload(self):
        self.data = {"version": 1, "remember": True, "tree_width": 240,
                     "details_width": 300, "theme": "light", "recent": []}
        try:
            if self.path.stat().st_size <= 65536:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._load(raw)
        except (FileNotFoundError, ValueError, TypeError):
            pass

    @contextmanager
    def _transaction(self):
        # Lock a stable sibling: the preferences file itself is atomically replaced.
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(self.path.suffix + ".lock").open("a+b") as handle:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    self._reload()
                    yield
                finally:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)

    def _load(self, raw):
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return
        if type(raw.get("remember")) is bool:
            self.data["remember"] = raw["remember"]
        if raw.get("theme") in THEMES:
            self.data["theme"] = raw["theme"]
        for key, low in (("tree_width", 160), ("details_width", 200)):
            if type(raw.get(key)) is int and low <= raw[key] <= 480:
                self.data[key] = raw[key]
        if self.data["remember"] and isinstance(raw.get("recent"), list):
            self.data["recent"] = [
                entry for entry in raw["recent"][:20]
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and len(entry["id"]) <= 64 and isinstance(entry.get("path"), str)
                and len(entry["path"]) <= 4096 and entry.get("input_format") in ("standard", "rdw")
                and type(entry.get("page")) is int and 1 <= entry["page"] <= 10_000_000
                and type(entry.get("zoom")) in (int, float) and math.isfinite(entry["zoom"])
                and .1 <= entry["zoom"] <= 8
            ]

    def snapshot(self):
        with self._transaction():
            return self._snapshot()

    def _snapshot(self):
        return json.loads(json.dumps(self.data))

    def _write(self):
        atomic_write_bytes(self.path, json.dumps(self.data, ensure_ascii=False).encode("utf-8"))

    def update(self, changes):
        if set(changes) - {"remember", "tree_width", "details_width", "clear_recent", "theme"}:
            raise ValueError("Unknown workspace setting.")
        if "theme" in changes and changes["theme"] not in THEMES:
            raise ValueError("Invalid workspace theme.")
        for key, low in (("tree_width", 160), ("details_width", 200)):
            if key in changes and (type(changes[key]) is not int or not low <= changes[key] <= 480):
                raise ValueError("Invalid panel width.")
        for key in ("remember", "clear_recent"):
            if key in changes and type(changes[key]) is not bool:
                raise ValueError("Invalid workspace setting.")
        with self._transaction():
            for key in ("remember", "tree_width", "details_width", "theme"):
                if key in changes:
                    self.data[key] = changes[key]
            if changes.get("clear_recent") or not self.data["remember"]:
                self.data["recent"] = []
            self._write()
            return self._snapshot()

    def record(self, document, page, zoom):
        if type(page) is not int or not 1 <= page <= max(document.page_count, 1):
            raise ValueError("Invalid page.")
        if type(zoom) not in (int, float) or not math.isfinite(zoom) or not .1 <= zoom <= 8:
            raise ValueError("Invalid zoom.")
        with self._transaction():
            if not self.data["remember"]:
                return self._snapshot()
            document.check_sources()
            path = str(document.path)
            old = next((row for row in self.data["recent"] if row["path"] == path), None)
            entry = {"id": old["id"] if old else secrets.token_urlsafe(18),
                     "path": path, "page": page, "zoom": zoom,
                     "input_format": document.index.input_format}
            self.data["recent"] = [entry] + [row for row in self.data["recent"] if row["path"] != path][:19]
            self._write()
            return self._snapshot()

    def find(self, identifier):
        if not isinstance(identifier, str) or len(identifier) > 64:
            return None
        with self._transaction():
            return next((dict(row) for row in self.data["recent"] if row["id"] == identifier), None)
