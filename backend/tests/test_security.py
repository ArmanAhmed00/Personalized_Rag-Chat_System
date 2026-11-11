"""Security, end to end and across every route (the per-feature checks live next to each feature).

* Non-loopback clients are refused without the token, and wrong tokens are refused.
* DNS-rebinding Host headers are refused, and state changes without the custom header are refused.
* Path traversal is blocked in uploads, downloads and restore.
* Malicious uploads (wrong content, oversize, polyglots) are refused.
* Code injection through analysis plans fails.
* Errors don't leak paths or keys, and security headers are present.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from outlawz.config import Settings
from outlawz.guard import OPEN_ROUTES, STATE_CHANGING
from outlawz.llm.base import shown_url
from tests.conftest import BASE_URL, CSRF, LOCAL, app_state, csv_bytes, make_client, pdf_bytes

REMOTE = ("203.0.113.9", 4000)


def api_routes() -> list[tuple[str, str]]:
    from outlawz.api import routers

    routes = []
    for route in (r for router in routers() for r in router.routes):
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            path = re.sub(r"\{[^}]+\}", "abc123", route.path)
            for method in route.methods:
                routes.append((method, path))
    return sorted(set(routes))


ROUTES = api_routes()
CHANGING = [r for r in ROUTES if r[0] in STATE_CHANGING]


def test_every_route_is_listed() -> None:
    assert len(ROUTES) > 50 and len(CHANGING) > 25


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_remote_clients_are_refused_everywhere(settings: Settings, method: str, path: str) -> None:
    with make_client(settings, client=REMOTE) as c:
        response = c.request(method, path, json={})
    if path in OPEN_ROUTES:
        assert response.status_code == 200
    else:
        assert response.status_code == 403, (method, path)
        assert response.json()["error"]["code"] == "forbidden_client"


@pytest.mark.parametrize(("method", "path"), CHANGING)
def test_state_changes_need_the_custom_header(settings: Settings, method: str, path: str) -> None:
    with make_client(settings, csrf=False) as c:
        response = c.request(method, path, json={})
    assert response.status_code == 403, (method, path)
    assert response.json()["error"]["code"] == "missing_header"


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r[1] not in OPEN_ROUTES][::7])
def test_token_is_checked_everywhere(settings: Settings, method: str, path: str) -> None:
    settings.access_token = "correct horse battery staple"
    with make_client(settings, client=REMOTE) as c:
        assert c.request(method, path, json={}).status_code == 401
        wrong = c.request(method, path, json={}, headers={"X-Outlawz-Token": "correct horse"})
        assert wrong.status_code == 401
        right = c.request(
            method, path, json={}, headers={"Authorization": "Bearer correct horse battery staple"}
        )
        assert right.status_code not in (401, 403), (method, path)


@pytest.mark.parametrize(
    "host", ["evil.example", "127.0.0.1.nip.io", "localhost.evil.example", "[::2]"]
)
def test_dns_rebinding_hosts_are_refused(settings: Settings, host: str) -> None:
    with make_client(settings) as c:
        for method, path in (
            ("GET", "/api/documents"),
            ("POST", "/api/chats"),
            ("GET", "/api/system/activity"),
        ):
            response = c.request(method, path, json={}, headers={"Host": host})
            assert (
                response.status_code == 403 and response.json()["error"]["code"] == "forbidden_host"
            )


def test_cross_site_post_with_header_but_foreign_origin_is_refused(client: TestClient) -> None:
    response = client.post("/api/chats", json={}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


# ---------------------------------------------------------------- path traversal


@pytest.mark.parametrize(
    "name", ["../../escape.pdf", "/etc/escape.pdf", "..\\..\\escape.pdf", "C:\\x\\escape.pdf"]
)
def test_upload_names_cannot_escape(client: TestClient, settings: Settings, name: str) -> None:
    response = client.post("/api/documents", files={"file": (name, pdf_bytes())})
    if response.status_code == 201:
        saved = response.json()["path"]
        assert ".." not in saved and not saved.startswith("/")
        assert (settings.library / saved).resolve().is_relative_to(settings.library.resolve())
    assert not (settings.library.parent / "escape.pdf").exists()


@pytest.mark.parametrize("folder", ["../outside", "/abs", "a/../../b", ".outlawz", ".outlawz/docs"])
def test_upload_folders_cannot_escape_or_touch_the_app_folder(
    client: TestClient, folder: str
) -> None:
    response = client.post(
        "/api/documents", files={"file": ("a.pdf", pdf_bytes())}, data={"folder": folder}
    )
    assert response.status_code in (400, 422), folder


def test_downloads_cannot_leave_the_library(client: TestClient, settings: Settings) -> None:
    doc = client.post("/api/documents", files={"file": ("a.pdf", pdf_bytes())}).json()["id"]
    secret = settings.library.parent / "secret.txt"
    secret.write_text("top secret")
    state = app_state(client)
    state.docs.update(doc, path="../secret.txt")
    response = client.get(f"/api/documents/{doc}/file")
    assert response.status_code == 404 and "top secret" not in response.text
    (settings.library / "link.pdf").symlink_to(secret)
    state.docs.update(doc, path="link.pdf")
    assert client.get(f"/api/documents/{doc}/file").status_code == 404
    for path in (
        "/api/documents/..%2F..%2Fsecret.txt/file",
        "/api/exports/..%2F..%2Fx/download",
        "/api/evals/..%2F..%2Fx",
    ):
        assert client.get(path).status_code in (400, 404)
    assert client.post("/api/backups/..%2Fsecret.txt/verify").status_code in (400, 404)


def test_restore_paths_are_covered_by_the_backup_tests() -> None:
    from tests import test_data

    assert hasattr(test_data, "test_path_traversal_in_restore_is_refused")
    assert hasattr(test_data, "test_links_in_restore_are_refused")


# ---------------------------------------------------------------- malicious uploads


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("invoice.pdf", b"MZ\x90\x00 this is a Windows program"),
        ("report.pdf", b"<html><script>alert(1)</script></html>"),
        ("data.csv", b"%PDF-1.7\n" + b"\x00" * 64),
        ("book.epub", _zip({"mimetype": b"text/plain", "x.txt": b"hello"})),
        ("poly.pdf", pdf_bytes() + _zip({"evil.txt": b"x"})),
        ("table.csv", b"PK\x03\x04" + b"\x00" * 30),
        ("script.py", b"print('hi')"),
    ],
)
def test_malicious_uploads_are_refused(
    client: TestClient, settings: Settings, name: str, data: bytes
) -> None:
    response = client.post("/api/documents", files={"file": (name, data)})
    assert response.status_code in (400, 415, 422), name
    assert not any(settings.library.glob(name))


def test_oversize_upload_is_refused_while_streaming(settings: Settings) -> None:
    settings.max_upload_mb = 1
    with make_client(settings) as c:
        response = c.post(
            "/api/documents", files={"file": ("big.csv", csv_bytes("a\n" + "1\n" * 700_000))}
        )
    assert response.status_code == 413 and response.json()["error"]["code"] == "too_large"
    assert not list(settings.library.glob("big*.csv"))


# ---------------------------------------------------------------- code injection


def test_code_injection_through_plans_fails(
    client: TestClient, settings: Settings, tmp_path: Any
) -> None:
    doc = client.post("/api/documents", files={"file": ("t.csv", csv_bytes())}).json()["id"]
    marker = tmp_path / "pwned"
    payloads = [
        {"op": "aggregate", "agg": "sum", "column": f"__import__('os').system('touch {marker}')"},
        {
            "op": "aggregate",
            "agg": "count",
            "filters": [{"column": "region", "op": "eq", "value": "@pd.io.common.os.system('id')"}],
        },
        {"op": "rows", "sort_by": "sales; import os"},
        {"op": "exec", "code": "import os"},
        {"op": "aggregate", "agg": "count", "__class__": "x"},
    ]
    for plan in payloads:
        response = client.post("/api/analysis/run", json={"doc_id": doc, "plan": plan})
        assert response.status_code in (200, 422)
        if response.status_code == 200:
            assert response.json()["result"]["rows"] == [[0]]
    assert not marker.exists()


# ---------------------------------------------------------------- errors and headers


def test_errors_do_not_leak_paths_or_keys(settings: Settings) -> None:
    settings.openai_key = "sk-live-0123456789"
    settings.llm = "openai"
    settings.openai_url = "http://admin:hunter2@127.0.0.1:9/v1?api_key=sk-query-secret"
    with make_client(settings, background=True) as c:
        app_state(c).llm_override = None
        # The settings page shows the address as the user typed it (so it can be edited), but
        # never the API key.
        assert "sk-live-0123456789" not in c.get("/api/settings").text
        bodies = [
            c.post("/api/settings/test", json={"changes": {}}).text,
            c.get("/api/config").text,
            c.get("/api/documents/nope").text,
            c.get("/api/nothing-here").text,
            c.post("/api/chats", json={"documents": "not a list"}).text,
        ]
        chat = c.post("/api/chats", json={}).json()["id"]
        c.post("/api/documents", files={"file": ("a.pdf", pdf_bytes(["The window is 30 days."]))})
        state = app_state(c)
        assert state.queue is not None
        c.portal.call(state.queue.wait_idle, 30)  # type: ignore[union-attr]
        bodies.append(
            c.post(f"/api/chats/{chat}/ask?stream=false", json={"question": "window?"}).text
        )
        bodies.append(c.post(f"/api/chats/{chat}/ask", json={"question": "window?"}).text)
    joined = "\n".join(bodies)
    for secret in (
        "sk-live-0123456789",
        "hunter2",
        "sk-query-secret",
        str(settings.data_dir),
        "Traceback",
        "secrets.json",
    ):
        assert secret not in joined, secret
    assert "127.0.0.1:9" in joined  # the address itself is shown, without the credentials


def test_shown_url_strips_credentials() -> None:
    assert (
        shown_url("https://user:pw@api.example.com:8443/v1?key=abc#x")
        == "https://api.example.com:8443/v1"
    )
    assert shown_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    "path", ["/api/documents", "/api/system/overview", "/api/evals", "/api/backups"]
)
def test_security_headers_on_api_routes(client: TestClient, path: str) -> None:
    response = client.get(path)
    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert re.fullmatch(r"[a-f0-9]{16}", headers["x-request-id"])


def test_streamed_answers_carry_the_headers_too(live_client: TestClient) -> None:
    chat = live_client.post("/api/chats", json={}).json()["id"]
    response = live_client.post(f"/api/chats/{chat}/ask", json={"question": "hello?"})
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_base_url_and_loopback_constants() -> None:
    assert (
        BASE_URL.startswith("http://127.0.0.1")
        and LOCAL[0] == "127.0.0.1"
        and CSRF == {"X-Outlawz": "1"}
    )
