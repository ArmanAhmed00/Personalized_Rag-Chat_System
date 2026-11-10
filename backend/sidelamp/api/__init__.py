"""HTTP routes, one module per area. Every route lives under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter


def routers() -> list[APIRouter]:
    from sidelamp.api import chats, documents, preferences, search, settings, system

    return [
        system.router,
        settings.router,
        documents.router,
        search.router,
        chats.router,
        preferences.router,
    ]
