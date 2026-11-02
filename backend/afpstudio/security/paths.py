"""Filesystem path validation.

The server accepts a local path from the browser, so every path is
resolved and checked against an allow-list of roots before it is opened.
Export targets additionally refuse to point at the file being analysed —
overwriting the source AFP would destroy the evidence the report is
based on.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

NUL = chr(0)


class PathError(ValueError):
    """Raised when a user-supplied path is rejected."""


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_under_roots(raw, roots: list[Path]) -> Path:
    """Resolve ``raw`` and require that it lives under one of ``roots``.

    Rejects traversal, UNC paths, device paths and anything that resolves
    outside the allow-list. Returns the resolved absolute path.
    """
    if raw is None or str(raw).strip() == "":
        raise PathError("No path supplied.")
    text = str(raw).strip()
    if NUL in text:
        raise PathError("Path contains a null byte.")
    if text.startswith("\\\\"):
        raise PathError("Network (UNC) paths are not allowed.")

    candidate = Path(text)
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except OSError as exc:
        raise PathError(f"Path cannot be resolved: {exc}") from exc

    if not resolved.is_file():
        raise PathError("Path is not a readable file.")

    for root in roots:
        if _is_within(resolved, root):
            return resolved
    raise PathError("Path is outside the permitted directories.")


def safe_output_path(raw, roots: list[Path], source: Path | None = None) -> Path:
    """Resolve an export target, refusing to overwrite the source file."""
    if raw is None or str(raw).strip() == "":
        raise PathError("No output path supplied.")
    candidate = Path(str(raw).strip()).expanduser()
    if not candidate.is_absolute():
        raise PathError("Output path must be absolute.")
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise PathError(f"Output path cannot be resolved: {exc}") from exc

    if resolved.name == "":
        raise PathError("Output path must name a file.")

    for root in roots:
        if _is_within(resolved, root):
            break
    else:
        raise PathError("Output path is outside the permitted directories.")

    if source is not None:
        try:
            source_resolved = source.resolve()
        except OSError:
            source_resolved = source
        if resolved == source_resolved:
            raise PathError("Refusing to write over the source AFP file.")
        if resolved.exists() and source_resolved.exists():
            try:
                if os.path.samefile(resolved, source_resolved):
                    raise PathError("Refusing to write over the source AFP file.")
            except OSError:
                pass

    return resolved


def atomic_write_bytes(target: Path, payload: bytes) -> Path:
    """Write ``payload`` to ``target`` via a temp file and one rename.

    A failed or interrupted write leaves the previous file untouched
    instead of a half-written one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target
