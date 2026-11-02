"""Response hardening headers.

readAFP's own policy allows a CDN and `eval` because its optional
Pyodide mode downloads a WebAssembly engine. Studio is local-only, so
the policy is tightened and the CDN is never referenced.
"""

from __future__ import annotations

from flask import Response


def studio_headers(allow_external: bool = False) -> dict[str, str]:
    """Return the header set applied to every Studio response."""
    connect = "'self'"
    script = "'self'"
    if allow_external:
        # Explicit opt-in for the Pyodide build only; never the default.
        connect += (
            " https://cdn.jsdelivr.net https://pypi.org"
            " https://files.pythonhosted.org"
        )
        script += " 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net"

    csp = (
        "default-src 'self'; "
        f"script-src {script}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self'; "
        f"connect-src {connect}; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    return {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": (
            "geolocation=(), microphone=(), camera=(), usb=(), payment=()"
        ),
    }


def apply_headers(response: Response, allow_external: bool = False) -> Response:
    for header, value in studio_headers(allow_external).items():
        response.headers.setdefault(header, value)
    return response
