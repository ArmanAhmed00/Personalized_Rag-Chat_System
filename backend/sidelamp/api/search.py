"""Search: hybrid by default, with explanations for every result."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from sidelamp.api.deps import State
from sidelamp.api.documents import passage_out
from sidelamp.search.service import Mode, hit_dict

router = APIRouter(prefix="/api", tags=["search"])


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Mode = "best"
    limit: int = Field(10, ge=1, le=50)
    documents: list[str] | None = Field(None, description="Only search these document ids")
    rerank: bool = True


@router.post("/search")
def search(body: SearchBody, state: State) -> dict[str, Any]:
    result = state.search.search(
        body.query, mode=body.mode, limit=body.limit, doc_ids=body.documents, rerank=body.rerank
    )
    hits = []
    for hit in result.hits:
        doc = state.docs.get(hit.doc_id)
        if doc is None:
            continue
        item = passage_out(doc, hit.passage)
        item["path"] = doc.path
        item.update(hit_dict(hit))
        hits.append(item)
    if state.on_search is not None:
        state.on_search(result)
    return {
        "query": result.query,
        "mode": result.mode,
        "hits": hits,
        "notes": result.notes,
        "timings": result.timings,
        "reranked": result.reranked,
        "searched_passages": result.searched_passages,
        "index": state.index.stats(),
    }
