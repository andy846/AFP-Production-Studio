"""CSRF protection for the local Studio UI.

The token is rendered into the page (so JavaScript can echo it back in
the ``X-CSRF-Token`` header) and only its digest is kept in the signed
session cookie. Requests must also come from the same origin.
"""

from __future__ import annotations

import hashlib
import secrets
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse

from flask import abort, request, session

TOKEN_BYTES = 32
HEADER_NAME = "X-CSRF-Token"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token() -> str:
    """Return a fresh token and remember its digest in the session."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session["csrf_token"] = token
    session["csrf_digest"] = _digest(token)
    session.modified = True
    return token


def current_token() -> str | None:
    """Return the raw token for this session, if one was issued."""
    stored = session.get("csrf_token")
    if stored and secrets.compare_digest(
        _digest(stored), session.get("csrf_digest", "")
    ):
        return stored
    return None


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    if origin is not None:
        return urlparse(origin).netloc == request.host
    referer = request.headers.get("Referer")
    if referer is None:
        return True
    return urlparse(referer).netloc == request.host


def validate() -> bool:
    """Check the request's CSRF token and origin."""
    if not _same_origin():
        return False
    expected = session.get("csrf_digest")
    if not expected:
        return False
    supplied = request.headers.get(HEADER_NAME, "")
    if not supplied:
        return False
    return secrets.compare_digest(_digest(supplied), expected)


def require_csrf(view: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for state-changing endpoints."""

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if request.method not in ("GET", "HEAD", "OPTIONS") and not validate():
            abort(403, "CSRF validation failed.")
        return view(*args, **kwargs)

    return wrapper
