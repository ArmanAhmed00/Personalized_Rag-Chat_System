"""Data analysis through the API, the sample documents, and computed answers in chat."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from outlawz.llm.base import Message
from outlawz.llm.fake import FakeLLM, default_responder
from tests.conftest import app_state, csv_bytes, wait_ingested

SALES = (
    "region,channel,units,revenue\n"
    "North,Online,2,20\nNorth,Store,4,40\nSouth,Online,1,10\nSouth,Online,3,30\n"
    "East,Store,5,50\nEast,Store,5,50\n"
)


def upload_csv(c: TestClient, name: str = "sales.csv", text: str = SALES) -> str:
    response = c.post("/api/documents", files={"file": (name, csv_bytes(text))})
    assert response.status_code == 201
    return str(response.json()["id"])


def scripted(plans: list[str], summary: str | None = None) -> FakeLLM:
    """A model that answers plan prompts from ``plans`` in turn, and summary prompts with
    ``summary`` (or the computed summary)."""
    queue = list(plans)

    def responder(messages: list[Message]) -> str:
        system = messages[0].content
        if "JSON plan" in system:
            return queue.pop(0) if queue else '{"op": "none"}'
        if "short summary" in system and summary is not None:
            return summary
        return default_responder(messages)

    return FakeLLM(responder)


def test_menu(client: TestClient) -> None:
    body = client.get("/api/analysis/menu").json()
    assert "aggregate" in body["ops"] and "between" in body["filter_ops"]
    assert body["periods"] == ["day", "week", "month", "quarter", "year"]


def test_overview_and_builder(live_client: TestClient) -> None:
    doc = upload_csv(live_client)
    overview = live_client.get(f"/api/analysis/{doc}/overview").json()
    assert overview["columns"][2] == {"name": "units", "kind": "number"}
    assert overview["overview"]["rows"] == 6 and overview["overview"]["duplicates"] == 1

    ran = live_client.post(
        "/api/analysis/run",
        json={
            "doc_id": doc,
            "plan": {"op": "aggregate", "agg": "sum", "column": "revenue", "by": ["region"]},
        },
    ).json()
    assert ran["result"]["rows"] == [["East", 100.0], ["North", 60.0], ["South", 40.0]]
    assert ran["result"]["chart"]["type"] == "bar"

    bad = live_client.post(
        "/api/analysis/run",
        json={"doc_id": doc, "plan": {"op": "aggregate", "agg": "sum", "column": "revnue"}},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["problems"] == [
        "There's no column 'revnue'. Did you mean 'revenue'?"
    ]
    off_menu = live_client.post(
        "/api/analysis/run", json={"doc_id": doc, "plan": {"op": "python", "code": "x"}}
    )
    assert off_menu.status_code == 422


def test_only_csv_files_can_be_analysed(live_client: TestClient) -> None:
    from tests.conftest import pdf_bytes

    pdf = live_client.post("/api/documents", files={"file": ("a.pdf", pdf_bytes())}).json()["id"]
    response = live_client.get(f"/api/analysis/{pdf}/overview")
    assert response.status_code == 422 and response.json()["error"]["code"] == "not_a_table"
    assert live_client.get("/api/analysis/nope/overview").status_code == 404


def test_ask_retries_an_invalid_plan_once(live_client: TestClient) -> None:
    doc = upload_csv(live_client)
    fake = scripted(
        [
            '{"op": "aggregate", "agg": "mean", "column": "revnue", "by": ["region"]}',
            '{"op": "aggregate", "agg": "mean", "column": "revenue", "by": ["region"]}',
        ]
    )
    app_state(live_client).llm_override = fake
    body = live_client.post(
        "/api/analysis/ask", json={"doc_id": doc, "question": "Average revenue by region?"}
    ).json()
    assert body["attempts"] == 2
    assert body["result"]["rows"][0] == ["East", 50.0]
    retry_prompt = fake.calls[1][-1].content
    assert "Did you mean 'revenue'?" in retry_prompt
    assert body["summary_source"] == "model"
    assert "COLUMNS:\n- region (text)" in fake.calls[0][1].content


def test_ask_gives_up_after_two_invalid_plans(live_client: TestClient) -> None:
    doc = upload_csv(live_client)
    app_state(live_client).llm_override = scripted(['{"op": "nope"}', "not json"])
    response = live_client.post("/api/analysis/ask", json={"doc_id": doc, "question": "Total?"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "no_plan"


def test_made_up_numbers_are_replaced_by_the_computed_summary(live_client: TestClient) -> None:
    doc = upload_csv(live_client)
    app_state(live_client).llm_override = scripted(
        ['{"op": "aggregate", "agg": "sum", "column": "revenue"}'],
        summary="Total revenue is 200, up 15% on last year.",
    )
    body = live_client.post(
        "/api/analysis/ask", json={"doc_id": doc, "question": "Total revenue?"}
    ).json()
    assert body["summary_source"] == "computed"
    assert body["rejected_numbers"] == ["15%"]
    assert body["summary"] == "The total of revenue is 200 (over 6 rows)."


def test_samples_route_is_idempotent(live_client: TestClient) -> None:
    first = live_client.post("/api/library/samples")
    assert first.status_code == 201
    names = sorted(d["name"] for d in first.json()["added"])
    assert names == [
        "Home Composting Guide.pdf",
        "The Lighthouse Keeper.epub",
        "lamp_sales_2024.csv",
        "lamp_sales_2025.csv",
    ]
    assert all(d["folder"] == "Samples" for d in first.json()["added"])
    assert live_client.post("/api/library/samples").json()["added"] == []
    wait_ingested(live_client)
    docs = live_client.get("/api/documents").json()["documents"]
    assert {d["status"] for d in docs} == {"ready"}
    hits = live_client.post(
        "/api/search", json={"query": "carbon to nitrogen ratio of dry leaves"}
    ).json()["hits"]
    assert hits[0]["document"] == "Home Composting Guide.pdf"
    story = live_client.post("/api/search", json={"query": "how many steps did Ines climb"}).json()[
        "hits"
    ]
    assert story[0]["document"] == "The Lighthouse Keeper.epub"

    ids = {d["name"]: d["id"] for d in docs}
    compared = live_client.post(
        "/api/analysis/compare",
        json={"a": ids["lamp_sales_2024.csv"], "b": ids["lamp_sales_2025.csv"]},
    ).json()
    assert compared["added"] == ["coupon"]


# ---------------------------------------------------------------- chat


@pytest.fixture
def csv_chat(live_client: TestClient) -> tuple[str, str]:
    doc = upload_csv(live_client)
    wait_ingested(live_client)
    chat = live_client.post("/api/chats", json={"documents": [doc]}).json()["id"]
    return doc, chat


def ask(c: TestClient, chat: str, question: str) -> dict[str, Any]:
    response = c.post(f"/api/chats/{chat}/ask?stream=false", json={"question": question})
    assert response.status_code == 200, response.text
    return dict(response.json()["message"])


def test_numeric_question_in_chat_gets_a_computed_answer(
    live_client: TestClient, csv_chat: tuple[str, str]
) -> None:
    doc, chat = csv_chat
    app_state(live_client).llm_override = scripted(
        ['{"op": "aggregate", "agg": "sum", "column": "revenue", "by": ["region"]}']
    )
    message = ask(live_client, chat, "What is the total revenue by region?")
    assert message["flags"] == ["computed"]
    computed = message["computed"]
    assert computed["doc_id"] == doc and computed["document"] == "sales.csv"
    assert computed["result"]["rows"][0] == ["East", 100.0]
    assert computed["result"]["chart"]["type"] == "bar"
    assert message["content"].startswith("The total of revenue is highest for East (100)")
    assert message["sources"] == []
    saved = live_client.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert saved["computed"]["result"]["rows"][0] == ["East", 100.0]


def test_streamed_computed_answer(live_client: TestClient, csv_chat: tuple[str, str]) -> None:
    _, chat = csv_chat
    app_state(live_client).llm_override = scripted(['{"op": "aggregate", "agg": "count"}'])
    text = live_client.post(
        f"/api/chats/{chat}/ask", json={"question": "How many rows are there?"}
    ).text
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    assert events[1] == {"sources": [], "computed": True}
    assert events[-1]["message"]["computed"]["result"]["rows"] == [[6]]


def test_question_that_does_not_fit_falls_back_to_passages(
    live_client: TestClient, csv_chat: tuple[str, str]
) -> None:
    _, chat = csv_chat
    app_state(live_client).llm_override = scripted(['{"op": "none"}'])
    message = ask(live_client, chat, "What is the average mood of the customers?")
    assert "computed" not in message["flags"]
    assert message["sources"] and message["sources"][0]["document"] == "sales.csv"


def test_non_numeric_question_skips_analysis(
    live_client: TestClient, csv_chat: tuple[str, str]
) -> None:
    _, chat = csv_chat
    fake = scripted(['{"op": "aggregate", "agg": "count"}'])
    app_state(live_client).llm_override = fake
    message = ask(live_client, chat, "Which regions appear in this file?")
    assert message["computed"] is None
    assert not any("JSON plan" in call[0].content for call in fake.calls)


def test_all_documents_chat_uses_a_csv_only_when_named(live_client: TestClient) -> None:
    upload_csv(live_client)
    wait_ingested(live_client)
    chat = live_client.post("/api/chats", json={}).json()["id"]
    app_state(live_client).llm_override = scripted(
        ['{"op": "aggregate", "agg": "sum", "column": "revenue"}'] * 2
    )
    plain = ask(live_client, chat, "What is the total?")
    assert plain["computed"] is None
    named = ask(live_client, chat, "What is the total revenue?")
    assert named["computed"] is not None and named["computed"]["result"]["rows"] == [[200.0]]
