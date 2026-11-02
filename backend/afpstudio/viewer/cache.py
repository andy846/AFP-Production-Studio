"""A byte budgeted cache of rendered pages.

Rendering a page is the expensive part of the viewer, and operators
bounce between the same pages constantly. Keeping every page in memory
is not an option for a 20 000 page job, so the cache holds pages until
it reaches its byte budget and then drops the least recently used ones.

The budget counts the size of the cached SVG, not the number of items,
because one page with a large inline image can outweigh a thousand text
pages.
"""

from __future__ import annotations
import threading
from collections import OrderedDict
from typing import Any


class PageCache:
    """Thread safe LRU cache with a byte budget."""

    def __init__(self, budget_bytes: int = 64 * 1024 * 1024) -> None:
        self.budget_bytes = max(0, int(budget_bytes))
        self._items: "OrderedDict[tuple[str, int], tuple[Any, int]]" = (
            OrderedDict()
        )
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @staticmethod
    def _size_of(value: Any) -> int:
        if isinstance(value, str):
            return len(value.encode("utf-8", errors="ignore"))
        if isinstance(value, (bytes, bytearray)):
            return len(value)
        return 64

    def get(self, document_id: str, page: int) -> Any | None:
        """Return the cached value for ``page``, or ``None``."""
        key = (document_id, page)
        with self._lock:
            if key not in self._items:
                self.misses += 1
                return None
            value, size = self._items.pop(key)
            self._items[key] = (value, size)
            self.hits += 1
            return value

    def put(self, document_id: str, page: int, value: Any) -> None:
        """Store a rendered page, evicting old pages when over budget."""
        size = self._size_of(value)
        key = (document_id, page)
        with self._lock:
            if key in self._items:
                old_size = self._items.pop(key)[1]
                self._bytes -= old_size
            if size > self.budget_bytes:
                # A single oversized page is simply not cached.
                return
            self._items[key] = (value, size)
            self._bytes += size
            while self._bytes > self.budget_bytes and self._items:
                _, (_, evicted_size) = self._items.popitem(last=False)
                self._bytes -= evicted_size
                self.evictions += 1

    def clear(self, document_id: str | None = None) -> None:
        """Drop cached pages, either for one document or for all of them."""
        with self._lock:
            if document_id is None:
                self._items.clear()
                self._bytes = 0
                return
            for key in list(self._items):
                if key[0] == document_id:
                    self._bytes -= self._items.pop(key)[1]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._items),
                "bytes": self._bytes,
                "budget_bytes": self.budget_bytes,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
            }
