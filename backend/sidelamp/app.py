"""The FastAPI app: middleware, error handling, routers and the lifespan that opens the library."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from sidelamp import APP_NAME, __version__
from sidelamp.config import Settings, load_settings
from sidelamp.errors import AppError
from sidelamp.guard import LocalAccessGuard
from sidelamp.middleware import BodyLimit, RequestContext, request_id_var
from sidelamp.state import AppState

log = logging.getLogger("sidelamp")

MULTIPART_OVERHEAD = 1024 * 1024


def create_app(
    settings: Settings | None = None,
    *,
    state: AppState | None = None,
    background: bool = True,
) -> FastAPI:
    """Build the app. Pass an already-opened ``state`` (the CLI does) or let the lifespan open
    one. ``background=False`` skips the watcher, workers and timers (useful in tests)."""
    if state is None:
        state = AppState(settings or load_settings())
    app_state = state

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        app_state.open()
        if background:
            app_state.start_background()
        try:
            yield
        finally:
            await app_state.close()

    app = FastAPI(
        title=f"{APP_NAME} API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.sidelamp = app_state

    def limit_for(method: str, path: str) -> int | None:
        if method == "POST" and path == "/api/documents":
            return app_state.settings.max_upload_mb * 1024 * 1024 + MULTIPART_OVERHEAD
        if method in {"POST", "PUT", "PATCH"}:
            return 2 * 1024 * 1024  # JSON bodies
        return None

    # Last added runs first: RequestContext → LocalAccessGuard → BodyLimit → routes.
    app.add_middleware(BodyLimit, limit_for=limit_for)
    app.add_middleware(LocalAccessGuard, get_settings=lambda: app_state.settings)
    app.add_middleware(RequestContext, observer=_observer(app_state))

    _install_error_handlers(app)

    from sidelamp.api import routers

    for router in routers():
        app.include_router(router)
    return app


def _observer(state: AppState) -> Any:
    def observe(method: str, path: str, route: str, status: int, ms: float) -> None:
        hook = getattr(state, "on_request", None)
        if hook is not None:
            hook(method, route, status, ms)

    return observe


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.to_dict(request_id_var.get() or None), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())[1:]) or "body",
                "problem": str(err.get("msg", "invalid value")),
            }
            for err in exc.errors()[:20]
        ]
        first = fields[0] if fields else {"field": "body", "problem": "invalid value"}
        error = AppError(
            "invalid_request",
            f"The request isn't valid: {first['field']}: {first['problem']}.",
            422,
            fields=fields,
        )
        return JSONResponse(error.to_dict(request_id_var.get() or None), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            error = AppError("not_found", "There's nothing at this address.", 404)
        elif exc.status_code == 405:
            error = AppError("method_not_allowed", "This address doesn't accept that method.", 405)
        else:
            error = AppError("http_error", "The request couldn't be completed.", exc.status_code)
        return JSONResponse(
            error.to_dict(request_id_var.get() or None), status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        request_id = request_id_var.get() or None
        # Type and request id only: a traceback could contain document text or paths.
        log.error("Unexpected %s (request %s)", type(exc).__name__, request_id)
        log.debug("Details", exc_info=exc)
        error = AppError(
            "internal_error",
            "Something went wrong on Sidelamp's side. Try again; if it keeps "
            "happening, the request id helps find what went wrong.",
            500,
        )
        return JSONResponse(error.to_dict(request_id), status_code=500)
