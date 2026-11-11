"""The local-access guard. There's no sign-in, so this is what keeps other people and other
websites out.

1. **Who may connect.** Only clients on this computer (loopback), unless the user set an access
   token (then every API request must carry it) or the app runs in Docker (where the port is
   published only on the host's loopback).
2. **DNS rebinding.** Only ``localhost``, ``127.0.0.1`` and ``[::1]`` Host headers are accepted,
   plus any the user explicitly allows. A malicious site that points its own domain at 127.0.0.1
   is refused because its Host header is wrong.
3. **CSRF.** Every state-changing request must carry the ``X-Outlawz`` header. Browsers never add
   custom headers to cross-site requests without a preflight, and cross-origin preflights are
   refused (except from origins listed in ``dev_origins``, for the frontend dev server).

``/api/health`` and ``/api/config`` stay open so the UI can show "the app isn't running" or
"enter your access token" screens.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
from collections.abc import Callable
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from outlawz.config import Settings

CSRF_HEADER = "x-outlawz"
TOKEN_HEADER = "x-outlawz-token"  # noqa: S105 - a header name, not a secret
OPEN_ROUTES = frozenset({"/api/health", "/api/config"})
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def host_name(host_header: str) -> str:
    """``localhost:8642`` → ``localhost``; ``[::1]:8642`` → ``::1``."""
    value = host_header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else ""
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def host_allowed(host_header: str, extra: list[str]) -> bool:
    name = host_name(host_header)
    if not name:
        return False
    return name in LOCAL_HOSTS or name in {h.strip().lower() for h in extra}


def _origin_matches_host(origin: str, host_header: str) -> bool:
    parts = urlsplit(origin)
    return bool(parts.netloc) and parts.netloc.lower() == host_header.strip().lower()


class LocalAccessGuard:
    """Pure ASGI middleware, so it also protects streaming responses."""

    def __init__(self, app: ASGIApp, get_settings: Callable[[], Settings]) -> None:
        self.app = app
        self.get_settings = get_settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = self.get_settings()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET").upper()
        client = scope.get("client")
        client_host = client[0] if client else None
        origin = headers.get("origin")
        host = headers.get("host", "")
        dev_origin = origin is not None and origin in settings.dev_origins

        if not host_allowed(host, settings.allowed_hosts):
            await _refuse(
                send,
                403,
                "forbidden_host",
                "Outlawz only answers requests addressed to localhost. If you meant to "
                "use it from another device, add this address to allowed_hosts.",
            )
            return

        is_api = path.startswith("/api/")
        open_route = path in OPEN_ROUTES
        token = settings.access_token
        loopback = is_loopback(client_host)

        if method == "OPTIONS":
            if dev_origin and origin is not None:
                await _preflight(send, origin)
            else:
                await _refuse(
                    send, 403, "cross_origin", "Requests from other websites aren't allowed."
                )
            return

        if token and is_api and not open_route:
            supplied = _token_from(headers)
            if not supplied or not hmac.compare_digest(supplied.encode(), token.encode()):
                await _refuse(
                    send,
                    401,
                    "bad_token",
                    "This request needs the access token. Enter it in Outlawz.",
                )
                return
        elif not token and not loopback and not settings.docker and not open_route:
            await _refuse(
                send,
                403,
                "forbidden_client",
                "Outlawz only accepts connections from this computer. To use it from "
                "another device, set an access token and put it behind HTTPS.",
            )
            return

        if method in STATE_CHANGING:
            if headers.get(CSRF_HEADER) != "1":
                await _refuse(
                    send,
                    403,
                    "missing_header",
                    "This request is missing the X-Outlawz header, so it was refused.",
                )
                return
            if origin and not dev_origin and not _origin_matches_host(origin, host):
                await _refuse(
                    send, 403, "cross_origin", "Requests from other websites aren't allowed."
                )
                return

        if dev_origin and origin is not None:
            await self.app(scope, receive, _with_cors(send, origin))
            return
        await self.app(scope, receive, send)


def _token_from(headers: dict[str, str]) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get(TOKEN_HEADER)


async def _refuse(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _preflight(send: Send, origin: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 204,
            "headers": [
                (b"access-control-allow-origin", origin.encode()),
                (b"access-control-allow-methods", b"GET, POST, PUT, PATCH, DELETE"),
                (
                    b"access-control-allow-headers",
                    b"content-type, x-outlawz, x-outlawz-token, authorization",
                ),
                (b"access-control-max-age", b"600"),
                (b"vary", b"Origin"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


def _with_cors(send: Send, origin: str) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            headers.append((b"access-control-allow-origin", origin.encode()))
            headers.append((b"access-control-expose-headers", b"x-request-id"))
            headers.append((b"vary", b"Origin"))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped
