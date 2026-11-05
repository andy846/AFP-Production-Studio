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
import secrets
import threading
from pathlib import Path

NUL = chr(0)


class PathError(ValueError):
    """Raised when a user-supplied path is rejected."""


def _validate_input(raw) -> str:
    if raw is None or not str(raw).strip():
        raise PathError("No path supplied.")
    text = str(raw).strip()
    if NUL in text or text.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        raise PathError("Device paths and null bytes are not allowed.")
    return text


def resolve_selected_file(raw) -> Path:
    """Resolve an explicit OS file selection without granting its directory."""
    text = _validate_input(raw)
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise PathError("Selected path must be absolute.")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise PathError("Path is not a readable file.")
        return resolved
    except FileNotFoundError as exc:
        raise PathError("File not found or network share is unavailable.") from exc
    except PermissionError as exc:
        raise PathError("Access denied. Check your file and network permissions.") from exc
    except OSError as exc:
        raise PathError(f"File or network share is unavailable: {exc}") from exc


class FileGrants:
    """Single-use capabilities issued only after a native dialog selection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens = {}

    def issue(self, raw, purpose):
        path = resolve_selected_file(raw)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = (path, purpose)
        return token

    def consume(self, token, raw, purpose):
        if not isinstance(token, str):
            raise PathError("Invalid file selection.")
        with self._lock:
            grant = self._tokens.pop(token, None)
        if not grant or grant[1] != purpose:
            raise PathError("File selection has expired; choose it again.")
        path = resolve_selected_file(raw)
        if path != grant[0]:
            raise PathError("File selection does not match the requested path.")
        return path


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_under_roots(raw, roots: list[Path]) -> Path:
    """Resolve ``raw`` and require that it lives under one of ``roots``.

    Rejects device paths and anything that resolves outside the allow-list.
    UNC paths require an explicitly configured UNC root.
    """
    text = _validate_input(raw)
    if text.startswith(("\\\\", "//")) and not any(str(root).startswith(("\\\\", "//")) for root in roots):
        raise PathError("Network path is outside the permitted directories.")

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
    candidate = Path(_validate_input(raw)).expanduser()
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
            except OSError as exc:
                raise PathError("Cannot verify the output is distinct from the source.") from exc

    return resolved


def publish_temporary(temporary: Path, target: Path, *, overwrite=False) -> None:
    """Publish a same-directory temporary file without an unconfirmed replacement."""
    if overwrite:
        os.replace(temporary, target)
    elif os.name == "nt":
        # Windows rename fails if the destination exists and does not need
        # hard-link support on the selected filesystem/share.
        os.rename(temporary, target)
    else:
        os.link(temporary, target)
        temporary.unlink()


def atomic_write_bytes(target: Path, payload: bytes, *, overwrite=True, verify=None) -> Path:
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
        if verify is not None:
            verify()
        publish_temporary(tmp_path, target, overwrite=overwrite)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target
