"""Serves the built web app (a Next.js static export) on the same port as the API.

A static export needs a few inline scripts. When the site is loaded, every inline script in every
HTML file is hashed, and each page is served with a Content-Security-Policy that allows exactly
those hashes and nothing else inline. Styles allow ``'unsafe-inline'`` (React sets style
attributes; low risk without inline script).
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
from pathlib import Path

from starlette.types import Receive, Scope, Send

from outlawz.store.safepath import UnsafePath, resolve_inside

WEB_DIR = Path(__file__).resolve().parent / "web"
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
}


def page_csp(script_hashes: list[str]) -> str:
    scripts = " ".join(["'self'", *(f"'sha256-{h}'" for h in sorted(set(script_hashes)))])
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {scripts}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "frame-src 'self' blob:",
            "worker-src 'none'",
            "object-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
        ]
    )


def inline_hashes(html: str) -> list[str]:
    return [
        base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        for body in _INLINE_SCRIPT.findall(html)
        if body.strip()
    ]


NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Outlawz</title>
<style>body{font-family:system-ui,sans-serif;max-width:36rem;margin:15vh auto;padding:0 1rem;
color:#14213d;background:#f5f7fa;line-height:1.5}code{background:#fff3b0;padding:.1rem .3rem}
</style></head><body><h1>Outlawz is running</h1><p>The web app isn't built into this copy, so
only the API is available. Build it with <code>python scripts/build_web.py</code>, or install a
release with <code>pipx install outlawz</code>.</p></body></html>"""


class StaticSite:
    """A small ASGI app: GET and HEAD only, files resolved safely inside the web folder."""

    def __init__(self, root: Path = WEB_DIR) -> None:
        self.root = root
        self.built = (root / "index.html").is_file()
        self._csp: dict[Path, str] = {}

    def _policy(self, path: Path) -> str:
        policy = self._csp.get(path)
        if policy is None:
            policy = page_csp(inline_hashes(path.read_text(encoding="utf-8", errors="replace")))
            self._csp[path] = policy
        return policy

    def _find(self, url_path: str) -> tuple[Path | None, str | None]:
        """Return (file, redirect). Directories serve their index.html."""
        rel = url_path.lstrip("/")
        if not rel:
            return self.root / "index.html", None
        try:
            target = resolve_inside(self.root, rel)
        except UnsafePath:
            return None, None
        if target.is_dir():
            index = target / "index.html"
            if not index.is_file():
                return None, None
            if not url_path.endswith("/"):
                return None, url_path + "/"
            return index, None
        if target.is_file():
            return target, None
        html = target.with_name(target.name + ".html")
        if html.is_file():
            return html, None
        return None, None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        method = scope.get("method", "GET")
        url_path: str = scope.get("path", "/")
        if url_path == "/api" or url_path.startswith("/api/"):
            await _api_not_found(send, scope)
            return
        if method not in ("GET", "HEAD"):
            await _plain(send, 405, b"Method not allowed")
            return
        if not self.built:
            await _respond(
                send, 200, NOT_BUILT.encode(), "text/html; charset=utf-8", page_csp([]), method
            )
            return
        file, redirect = self._find(url_path)
        if redirect is not None:
            query = scope.get("query_string", b"").decode("latin-1")
            location = redirect + (f"?{query}" if query else "")
            await send(
                {
                    "type": "http.response.start",
                    "status": 308,
                    "headers": [
                        (b"location", location.encode("latin-1")),
                        (b"content-length", b"0"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        status = 200
        if file is None:
            status = 404
            file = self.root / "404.html"
            if not file.is_file():
                await _plain(send, 404, b"Not found")
                return
        content_type = (
            _TYPES.get(file.suffix)
            or mimetypes.guess_type(file.name)[0]
            or "application/octet-stream"
        )
        csp = self._policy(file) if file.suffix == ".html" else None
        cache = (
            "public, max-age=31536000, immutable"
            if url_path.startswith("/_next/static/")
            else "no-cache"
        )
        await _respond(send, status, file.read_bytes(), content_type, csp, method, cache)


async def _respond(
    send: Send,
    status: int,
    body: bytes,
    content_type: str,
    csp: str | None,
    method: str,
    cache: str = "no-cache",
) -> None:
    headers = [
        (b"content-type", content_type.encode()),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", cache.encode()),
    ]
    if csp:
        headers.append((b"content-security-policy", csp.encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if method == "HEAD" else body})


async def _plain(send: Send, status: int, body: bytes) -> None:
    await _respond(send, status, body, "text/plain; charset=utf-8", None, "GET")


async def _api_not_found(send: Send, scope: Scope) -> None:
    """Unknown API addresses get the API's JSON error, not a web page."""
    import json

    request_id = (scope.get("state") or {}).get("request_id")
    error: dict[str, str] = {"code": "not_found", "message": "There's nothing at this address."}
    if request_id:
        error["request_id"] = str(request_id)
    body = json.dumps({"error": error}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
