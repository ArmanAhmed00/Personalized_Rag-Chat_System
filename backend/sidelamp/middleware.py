"""Request context: a request id on every response, security headers, size limits and timing.

Errors are turned into JSON in ``app.py`` (exception handlers); this module only adds headers and
measures. Everything here is pure ASGI so streaming answers aren't buffered.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("sidelamp")

REQUEST_ID_HEADER = "x-request-id"
_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
STATIC_CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"

_COMMON_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
]

# Called after each request with (method, path, route template, status, milliseconds).
RequestObserver = Callable[[str, str, str, int, float], None]


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestContext:
    def __init__(self, app: ASGIApp, observer: RequestObserver | None = None) -> None:
        self.app = app
        self.observer = observer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = ""
        for key, value in scope["headers"]:
            if key.lower() == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = incoming if _VALID_ID.match(incoming) else new_request_id()
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        path: str = scope.get("path", "")
        is_api = path.startswith("/api/")
        status_holder = {"status": 500}
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers.append((b"x-request-id", request_id.encode()))
                for key, value in _COMMON_HEADERS:
                    if key not in present:
                        headers.append((key, value))
                if b"content-security-policy" not in present:
                    csp = API_CSP if is_api else STATIC_CSP
                    headers.append((b"content-security-policy", csp.encode()))
                if is_api and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        started_response = {"value": False}

        async def tracking_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                started_response["value"] = True
            await send_wrapper(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            # Never show a traceback: it could contain document text, paths or keys.
            log.error("Unexpected %s (request %s)", type(exc).__name__, request_id)
            log.debug("Details", exc_info=exc)
            if not started_response["value"]:
                await _internal_error(tracking_send, request_id)
        finally:
            request_id_var.reset(token)
            if self.observer is not None:
                elapsed = (time.perf_counter() - started) * 1000
                route = scope.get("route")
                template = getattr(route, "path", None) or ("/api/*" if is_api else "/static")
                with contextlib.suppress(Exception):  # metrics must never break a request
                    self.observer(
                        scope.get("method", "GET"), path, template, status_holder["status"], elapsed
                    )


async def _internal_error(send: Send, request_id: str) -> None:
    body = json.dumps(
        {
            "error": {
                "code": "internal_error",
                "message": "Something went wrong on Sidelamp's side. Try again; if it keeps "
                "happening, the request id helps find what went wrong.",
                "request_id": request_id,
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Refuse request bodies over a limit while they stream in, before they're spooled to disk.

    ``limit_for`` returns the byte limit for a (method, path) or ``None`` for no limit.
    """

    def __init__(self, app: ASGIApp, limit_for: Callable[[str, str], int | None]) -> None:
        self.app = app
        self.limit_for = limit_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope.get("method", "GET"), scope.get("path", ""))
        if limit is None:
            await self.app(scope, receive, send)
            return
        for key, value in scope["headers"]:
            if key.lower() == b"content-length":
                try:
                    if int(value) > limit:
                        await _too_large(send, limit)
                        return
                except ValueError:
                    pass
        received = 0
        started = {"value": False}

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                started["value"] = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if not started["value"]:
                await _too_large(send, limit)


async def _too_large(send: Send, limit: int) -> None:
    mb = max(1, limit // (1024 * 1024))
    body = json.dumps(
        {
            "error": {
                "code": "too_large",
                "message": f"This upload is larger than the {mb} MB limit. You can raise the "
                f"limit in Settings, or copy the file into the library folder directly.",
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
