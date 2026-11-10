"""HTTP routes, one module per area. Every route lives under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter


def routers() -> list[APIRouter]:
    from sidelamp.api import documents, system

    return [system.router, documents.router]
