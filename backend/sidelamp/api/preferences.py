"""Saved preferences: switch on or off, add, edit, delete, clear, and save or dismiss the
suggestions that "Remember that…" messages create."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from sidelamp.api.deps import State

router = APIRouter(prefix="/api", tags=["preferences"])


class Switch(BaseModel):
    enabled: bool


class Item(BaseModel):
    text: str = Field(min_length=1, max_length=300)


@router.get("/preferences")
def get_preferences(state: State) -> dict[str, Any]:
    return state.preferences.public()


@router.put("/preferences")
def switch_preferences(body: Switch, state: State) -> dict[str, Any]:
    state.preferences.set_enabled(body.enabled)
    state.events.emit("preferences_changed", action="enabled" if body.enabled else "disabled")
    return state.preferences.public()


@router.post("/preferences/items", status_code=201)
def add_preference(body: Item, state: State) -> dict[str, Any]:
    item = state.preferences.add(body.text)
    state.events.emit("preferences_changed", action="added", item_id=item.id)
    return item.model_dump(mode="json")


@router.patch("/preferences/items/{item_id}")
def edit_preference(item_id: str, body: Item, state: State) -> dict[str, Any]:
    item = state.preferences.edit(item_id, body.text)
    state.events.emit("preferences_changed", action="edited", item_id=item.id)
    return item.model_dump(mode="json")


@router.delete("/preferences/items/{item_id}")
def delete_preference(item_id: str, state: State) -> dict[str, Any]:
    state.preferences.delete(item_id)
    state.events.emit("preferences_changed", action="deleted", item_id=item_id)
    return {"deleted": item_id}


@router.delete("/preferences/items")
def clear_preferences(state: State) -> dict[str, Any]:
    count = state.preferences.clear()
    state.events.emit("preferences_changed", action="cleared", count=count)
    return {"cleared": count}


@router.post("/preferences/suggestions/{suggestion_id}/save", status_code=201)
def save_suggestion(suggestion_id: str, state: State) -> dict[str, Any]:
    item = state.preferences.accept(suggestion_id)
    state.events.emit("preferences_changed", action="added", item_id=item.id)
    return item.model_dump(mode="json")


@router.delete("/preferences/suggestions/{suggestion_id}")
def dismiss_suggestion(suggestion_id: str, state: State) -> dict[str, Any]:
    state.preferences.dismiss(suggestion_id)
    return {"dismissed": suggestion_id}
