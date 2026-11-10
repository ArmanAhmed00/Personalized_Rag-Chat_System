"""Shared helpers for routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from sidelamp.state import AppState


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.sidelamp
    return state


State = Annotated[AppState, Depends(get_state)]
