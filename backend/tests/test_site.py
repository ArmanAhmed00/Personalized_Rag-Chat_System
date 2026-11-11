"""The built web app: same-port static serving with a per-page script-hash CSP."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from outlawz.site import NOT_BUILT, StaticSite, inline_hashes, page_csp

INLINE = "self.__next_f=self.__next_f||[]"


def _hash(body: str) -> str:
    return base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "library").mkdir(parents=True)
    (root / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "index.html").write_text(
        f'<!doctype html><html><head><script src="/_next/static/chunks/a.js"></script>'
        f"</head><body><script>{INLINE}</script></body></html>"
    )
    (root / "library" / "index.html").write_text("<!doctype html><title>Library</title>")
    (root / "404.html").write_text("<!doctype html><title>Not found</title>")
    (root / "_next" / "static" / "chunks" / "a.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    return root


def test_inline_hashes_skip_external_and_empty_scripts() -> None:
    html = f'<script src="x.js"></script><script>  </script><script>{INLINE}</script>'
    assert inline_hashes(html) == [_hash(INLINE)]


def test_csp_allows_only_hashed_inline_scripts() -> None:
    policy = page_csp([_hash(INLINE)])
    script_src = next(p for p in policy.split("; ") if p.startswith("script-src"))
    assert f"'sha256-{_hash(INLINE)}'" in script_src
    assert "unsafe-inline" not in script_src
    assert "unsafe-eval" not in script_src
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy


def test_index_is_served_with_its_own_policy(site: Path) -> None:
    client = TestClient(StaticSite(site))
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f"'sha256-{_hash(INLINE)}'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-cache"


def test_folder_pages_redirect_to_trailing_slash_keeping_the_query(site: Path) -> None:
    client = TestClient(StaticSite(site), follow_redirects=False)
    response = client.get("/library?doc=abc")
    assert response.status_code == 308
    assert response.headers["location"] == "/library/?doc=abc"
    page = client.get("/library/")
    assert page.status_code == 200
    assert "Library" in page.text


def test_hashed_assets_are_cached_for_a_year(site: Path) -> None:
    response = TestClient(StaticSite(site)).get("/_next/static/chunks/a.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "immutable" in response.headers["cache-control"]
    assert "content-security-policy" not in response.headers


def test_unknown_pages_get_the_404_page(site: Path) -> None:
    response = TestClient(StaticSite(site)).get("/nowhere/")
    assert response.status_code == 404
    assert "Not found" in response.text


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/%2e%2e/secret.txt", "/library/../../secret.txt"]
)
def test_paths_cannot_leave_the_web_folder(site: Path, path: str) -> None:
    response = TestClient(StaticSite(site)).get(path)
    assert "outside" not in response.text
    assert response.status_code in (404, 400)


def test_only_get_and_head(site: Path) -> None:
    client = TestClient(StaticSite(site))
    assert client.post("/").status_code == 405
    head = client.head("/")
    assert head.status_code == 200
    assert head.content == b""


def test_unknown_api_paths_get_a_json_error(site: Path) -> None:
    response = TestClient(StaticSite(site)).get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_without_a_build_a_helpful_page_is_shown(tmp_path: Path) -> None:
    response = TestClient(StaticSite(tmp_path / "missing")).get("/library/")
    assert response.status_code == 200
    assert response.text == NOT_BUILT
    assert "script-src 'self'" in response.headers["content-security-policy"]
