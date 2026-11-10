"""Settings: read, change, and "Test connection" for the model.

Secrets are never sent back: the browser only learns whether each key is set. Settings fixed by an
environment variable are listed as locked and can't be changed here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from sidelamp.api.deps import State
from sidelamp.config import EDITABLE_FIELDS, SECRET_FIELDS, Settings, save_settings
from sidelamp.errors import AppError
from sidelamp.llm.base import make_llm

router = APIRouter(prefix="/api", tags=["settings"])

# Settings the model test may try before they're saved.
MODEL_FIELDS = frozenset(
    {"llm", "llm_model", "ollama_url", "openai_url", "openai_key", "anthropic_key", "llm_timeout"}
)


class Changes(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


def settings_out(settings: Settings) -> dict[str, Any]:
    return {
        "settings": settings.public(),
        "locked": sorted(settings.locked),
        "editable": sorted(EDITABLE_FIELDS),
        "secrets": sorted(SECRET_FIELDS),
        "cloud": settings.is_cloud_llm,
    }


@router.get("/settings")
def get_settings(state: State) -> dict[str, Any]:
    return settings_out(state.settings)


@router.put("/settings")
def change_settings(body: Changes, state: State) -> dict[str, Any]:
    # An empty secret means "keep the current one" (the UI never sees the old value).
    changes = {
        k: v for k, v in body.changes.items() if not (k in SECRET_FIELDS and v in ("", None))
    }
    if not changes:
        return {**settings_out(state.settings), "notes": []}
    updated = save_settings(state.settings, changes)
    notes = state.apply_settings(updated)
    state.events.emit("settings_changed", keys=sorted(changes))
    return {**settings_out(updated), "notes": notes}


@router.post("/settings/test")
async def test_connection(body: Changes, state: State) -> dict[str, Any]:
    """Check the model connection with the current settings, or with unsaved ones."""
    candidate = {k: v for k, v in body.changes.items() if k in MODEL_FIELDS and v not in ("", None)}
    unknown = set(body.changes) - MODEL_FIELDS
    if unknown:
        raise AppError("invalid_setting", "Only model settings can be tested.", 422)
    if candidate:
        merged = state.settings.model_dump()
        merged.update(candidate)
        try:
            settings = Settings(**merged)
        except ValueError:
            raise AppError("invalid_setting", "One of those values isn't valid.", 422) from None
        llm = make_llm(settings)
    else:
        llm = state.llm()
    result = await llm.check()
    return {"ok": result.ok, "message": result.message, "models": result.models[:50]}
