"""Shared helpers for routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from outlawz.config import Settings
from outlawz.state import AppState


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.outlawz
    return state


State = Annotated[AppState, Depends(get_state)]


async def reopen(request: Request, settings: Settings) -> AppState:
    """Close the current library and open one with ``settings`` (restore, switching libraries).
    If the new one can't be opened, the old settings are opened again and the error raised."""
    old: AppState = request.app.state.outlawz
    background, watch = getattr(request.app.state, "background", (True, True))
    await old.close()
    fresh = AppState(settings)
    try:
        fresh.open()
    except Exception:
        fresh = AppState(old.settings)
        fresh.open()
        if background:
            fresh.start_background(watch=watch)
        request.app.state.outlawz = fresh
        raise
    if background:
        fresh.start_background(watch=watch)
    request.app.state.outlawz = fresh
    return fresh
