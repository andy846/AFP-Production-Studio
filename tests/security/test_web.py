"""Response headers, CSRF and the local-only guarantees."""

from __future__ import annotations

import re

from afpstudio.security.csrf import HEADER_NAME
from afpstudio.security.headers import studio_headers


def test_the_policy_has_no_cdn_and_no_eval():
    headers = studio_headers()
    policy = headers["Content-Security-Policy"]
    assert "cdn" not in policy.lower()
    assert "unsafe-eval" not in policy
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "base-uri 'none'" in policy


def test_the_hardening_headers_are_present():
    headers = studio_headers()
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "geolocation=()" in headers["Permissions-Policy"]


def test_the_external_opt_in_is_off_by_default():
    assert "cdn" not in studio_headers()["Content-Security-Policy"].lower()
    assert "cdn" in studio_headers(True)["Content-Security-Policy"].lower()


def test_the_app_sends_the_headers(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"


def test_a_state_change_without_a_token_is_refused(app_client, tmp_path):
    from afpstudio.testing import synthetic

    target = tmp_path / "job.afp"
    target.write_bytes(synthetic.simplex_document(1))
    response = app_client.post(
        "/api/documents/open", json={"path": str(target)}
    )
    assert response.status_code == 403
    assert response.get_data(as_text=True) or response.get_json() is None


def test_a_state_change_with_a_wrong_token_is_refused(app_client, tmp_path):
    from afpstudio.testing import synthetic

    target = tmp_path / "job.afp"
    target.write_bytes(synthetic.simplex_document(1))
    response = app_client.post(
        "/api/documents/open",
        json={"path": str(target)},
        headers={HEADER_NAME: "not-the-token"},
    )
    assert response.status_code == 403


def test_a_state_change_with_the_token_is_accepted(app_client, tmp_path):
    from afpstudio.testing import synthetic

    target = tmp_path / "job.afp"
    target.write_bytes(synthetic.simplex_document(1))
    token = app_client.get("/api/csrf").get_json()["token"]
    response = app_client.post(
        "/api/documents/open",
        json={"path": str(target)},
        headers={HEADER_NAME: token},
    )
    assert response.status_code == 200


def test_a_path_outside_the_allowed_roots_is_refused(app_client):
    token = app_client.get("/api/csrf").get_json()["token"]
    response = app_client.post(
        "/api/documents/open",
        json={"path": "C:\\Windows\\System32\\drivers\\etc\\hosts"},
        headers={HEADER_NAME: token},
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_path"


def test_the_page_contains_no_external_reference(app_client):
    body = app_client.get("/").get_data(as_text=True)
    for pattern in ("http://", "https://", "//cdn", "cdn.jsdelivr"):
        assert pattern not in body
    assert not re.search(r"<script[^>]+src=\"http", body)
