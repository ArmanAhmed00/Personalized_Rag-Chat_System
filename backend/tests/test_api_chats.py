"""Chat through the API: streaming, citations, follow-ups, interruption, preferences, settings."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from outlawz.chat.answer import AnswerRun
from outlawz.config import Settings
from outlawz.llm.base import LLMError, Message
from outlawz.llm.fake import FakeLLM, default_responder
from tests.conftest import app_state, make_client, wait_ingested
from tests.test_api_search import add_pdf


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        name, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        events.append((name, json.loads(data)))
    return events


@pytest.fixture
def library(live_client: TestClient, settings: Settings) -> dict[str, str]:
    ids = {
        "refunds": add_pdf(
            live_client,
            settings,
            "refunds.pdf",
            "The refund window is 30 days from delivery. Keep the receipt.",
        ),
        "shipping": add_pdf(
            live_client,
            settings,
            "shipping.pdf",
            "Standard shipping takes 5 business days. Express shipping takes 2 days.",
        ),
    }
    wait_ingested(live_client)
    return ids


def new_chat(c: TestClient, **body: Any) -> str:
    response = c.post("/api/chats", json=body)
    assert response.status_code == 201
    return str(response.json()["id"])


def test_ask_streams_events_and_saves_a_cited_answer(
    live_client: TestClient, library: dict[str, str]
) -> None:
    chat = new_chat(live_client)
    response = live_client.post(
        f"/api/chats/{chat}/ask", json={"question": "How long is the refund window?"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [n for n, _ in events]
    assert names[0] == "start" and names[1] == "sources" and names[-1] == "done"
    assert "delta" in names
    sources = events[1][1]["sources"]
    assert sources[0]["document"] == "refunds.pdf" and sources[0]["n"] == 1
    assert sources[0]["page"] == 1
    done = events[-1][1]["message"]
    assert done["status"] == "complete"
    assert "30 days" in done["content"] and "[1]" in done["content"]
    assert done["cited"] == [1] and done["flags"] == []
    assert done["timings"]["first_word_ms"] >= 0
    streamed = "".join(d["text"] for n, d in events if n == "delta")
    assert "30 days" in streamed

    saved = live_client.get(f"/api/chats/{chat}").json()
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["title"] == "How long is the refund window?"
    assert saved["messages"][1]["sources"][0]["text"].startswith("The refund window")


def test_ask_without_streaming_returns_json(
    live_client: TestClient, library: dict[str, str]
) -> None:
    chat = new_chat(live_client)
    body = live_client.post(
        f"/api/chats/{chat}/ask?stream=false",
        json={"question": "How long does express shipping take?"},
    ).json()
    message = body["message"]
    assert message["status"] == "complete"
    assert message["sources"][0]["document"] == "shipping.pdf"


def test_model_inventing_citations_is_cleaned(
    live_client: TestClient, library: dict[str, str]
) -> None:
    app_state(live_client).llm_override = FakeLLM(
        lambda m: "The window is 30 days [1][14]. Also something else [9]."
    )
    chat = new_chat(live_client)
    message = live_client.post(
        f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window"}
    ).json()["message"]
    assert message["content"] == "The window is 30 days [1]. Also something else."
    assert "removed_citations" in message["flags"]


def test_answer_with_no_valid_citation_is_flagged(
    live_client: TestClient, library: dict[str, str]
) -> None:
    app_state(live_client).llm_override = FakeLLM(lambda m: "It is about a month.")
    chat = new_chat(live_client)
    message = live_client.post(
        f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window"}
    ).json()["message"]
    assert "no_citations" in message["flags"]


def test_no_documents_ready_gives_a_plain_answer(live_client: TestClient) -> None:
    chat = new_chat(live_client)
    message = live_client.post(
        f"/api/chats/{chat}/ask?stream=false", json={"question": "anything?"}
    ).json()["message"]
    assert message["flags"] == ["no_sources"]
    assert "couldn't find anything" in message["content"]


def test_follow_up_is_rewritten_with_history(
    live_client: TestClient, library: dict[str, str]
) -> None:
    calls: list[list[Message]] = []

    def responder(messages: list[Message]) -> str:
        calls.append(messages)
        if "standalone search query" in messages[0].content:
            return "how long does express shipping take"
        return default_responder(messages)

    app_state(live_client).llm_override = FakeLLM(responder)
    chat = new_chat(live_client)
    live_client.post(
        f"/api/chats/{chat}/ask?stream=false",
        json={"question": "How long does standard shipping take?"},
    )
    calls.clear()
    message = live_client.post(
        f"/api/chats/{chat}/ask?stream=false", json={"question": "and express?"}
    ).json()["message"]
    assert message["query"] == "how long does express shipping take"
    rewrite_call = calls[0]
    assert "User: How long does standard shipping take?" in rewrite_call[1].content
    assert "FOLLOW-UP: and express?" in rewrite_call[1].content
    assert message["sources"][0]["document"] == "shipping.pdf"


def test_document_selection_limits_sources(
    live_client: TestClient, library: dict[str, str]
) -> None:
    chat = new_chat(live_client, documents=[library["shipping"]])
    message = live_client.post(
        f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window 30 days"}
    ).json()["message"]
    assert {s["document"] for s in message["sources"]} == {"shipping.pdf"}
    # Unknown documents are refused; all_documents switches back to every document.
    assert live_client.patch(f"/api/chats/{chat}", json={"documents": ["nope"]}).status_code == 422
    changed = live_client.patch(f"/api/chats/{chat}", json={"all_documents": True}).json()
    assert changed["documents"] is None


def test_deleting_a_document_wipes_its_quotes(
    live_client: TestClient, library: dict[str, str]
) -> None:
    chat = new_chat(live_client, documents=[library["refunds"], library["shipping"]])
    live_client.post(f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window"})
    live_client.delete(f"/api/documents/{library['refunds']}")
    saved = live_client.get(f"/api/chats/{chat}").json()
    assert saved["documents"] == [library["shipping"]]
    source = saved["messages"][1]["sources"][0]
    assert source["deleted"] is True and source["text"] is None
    assert "Keep the receipt" not in json.dumps(saved)  # only the passage quoted this


def test_model_error_is_saved_and_reported(
    live_client: TestClient, library: dict[str, str]
) -> None:
    app_state(live_client).llm_override = FakeLLM(
        error=LLMError(
            "Ollama isn't answering at http://127.0.0.1:11434. Is it running?", "unreachable"
        )
    )
    chat = new_chat(live_client)
    events = parse_sse(
        live_client.post(f"/api/chats/{chat}/ask", json={"question": "refund window"}).text
    )
    assert events[-1][0] == "error" and events[-1][1]["code"] == "unreachable"
    saved = live_client.get(f"/api/chats/{chat}").json()
    assert saved["messages"][-1]["status"] == "error"
    response = live_client.post(f"/api/chats/{chat}/ask?stream=false", json={"question": "again"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "unreachable"


def test_interrupted_answer_keeps_the_partial_text(
    live_client: TestClient, library: dict[str, str]
) -> None:
    state = app_state(live_client)
    state.llm_override = FakeLLM(
        lambda m: "The refund window is 30 days from delivery [1]. " * 4, chunk=6, delay=0.02
    )
    chat = new_chat(live_client)

    async def ask_then_disconnect() -> None:
        run = AnswerRun(state, chat, "How long is the refund window?")

        async def consume() -> None:
            async for event in run.events():
                if event.name == "delta" and len(run.parts) >= 3:
                    await asyncio.sleep(10)  # the browser is gone; we get cancelled here

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    live_client.portal.call(ask_then_disconnect)  # type: ignore[union-attr]
    saved = live_client.get(f"/api/chats/{chat}").json()
    message = saved["messages"][-1]
    assert message["status"] == "interrupted" and "interrupted" in message["flags"]
    assert message["content"] and message["content"].startswith("The refund")
    assert len(message["content"]) < len("The refund window is 30 days from delivery [1]. " * 4)


def test_remember_that_creates_a_suggestion_used_only_after_save(
    live_client: TestClient, library: dict[str, str]
) -> None:
    state = app_state(live_client)
    fake = FakeLLM()
    state.llm_override = fake
    chat = new_chat(live_client)
    message = live_client.post(
        f"/api/chats/{chat}/ask?stream=false",
        json={"question": "Remember that I prefer bullet points"},
    ).json()["message"]
    suggestion = message["suggestion"]
    assert suggestion["text"] == "I prefer bullet points."
    assert fake.calls == []  # no model call, no search: it's a preference, not a question
    assert live_client.get("/api/preferences").json()["items"] == []

    live_client.post(f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window"})
    assert "I prefer bullet points." not in fake.calls[-1][0].content

    saved = live_client.post(f"/api/preferences/suggestions/{suggestion['id']}/save")
    assert saved.status_code == 201
    live_client.post(f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window"})
    assert "- I prefer bullet points." in fake.calls[-1][0].content

    live_client.put("/api/preferences", json={"enabled": False})
    live_client.post(f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window"})
    assert "bullet points" not in fake.calls[-1][0].content


def test_preference_routes(client: TestClient) -> None:
    item = client.post("/api/preferences/items", json={"text": "Keep answers short."}).json()
    client.patch(f"/api/preferences/items/{item['id']}", json={"text": "Keep answers very short."})
    assert client.get("/api/preferences").json()["items"][0]["text"] == "Keep answers very short."
    client.post("/api/preferences/items", json={"text": "Use metric units."})
    assert client.delete("/api/preferences/items").json() == {"cleared": 2}
    assert client.delete("/api/preferences/items/nope").status_code == 404
    assert client.post("/api/preferences/items", json={"text": ""}).status_code == 422


def test_chat_crud_and_feedback(live_client: TestClient, library: dict[str, str]) -> None:
    chat = new_chat(live_client)
    live_client.post(f"/api/chats/{chat}/ask?stream=false", json={"question": "refund window"})
    renamed = live_client.patch(f"/api/chats/{chat}", json={"title": "  Refunds  "}).json()
    assert renamed["title"] == "Refunds" and renamed["titled"] is True
    listed = live_client.get("/api/chats").json()["chats"]
    assert listed[0]["id"] == chat and listed[0]["messages"] == 2
    answer = live_client.get(f"/api/chats/{chat}").json()["messages"][1]
    up = live_client.put(
        f"/api/chats/{chat}/messages/{answer['id']}/feedback", json={"value": "up"}
    ).json()
    assert up["feedback"] == "up"
    question = live_client.get(f"/api/chats/{chat}").json()["messages"][0]
    assert (
        live_client.put(
            f"/api/chats/{chat}/messages/{question['id']}/feedback", json={"value": "up"}
        ).status_code
        == 404
    )  # only answers take feedback
    assert live_client.delete(f"/api/chats/{chat}").json() == {"deleted": chat}
    assert live_client.get(f"/api/chats/{chat}").status_code == 404
    assert live_client.get("/api/chats/..%2F..%2Fetc").status_code == 404


def test_question_limits(client: TestClient) -> None:
    chat = new_chat(client)
    assert client.post(f"/api/chats/{chat}/ask", json={"question": ""}).status_code == 422
    assert client.post(f"/api/chats/{chat}/ask", json={"question": "x" * 5000}).status_code == 422
    assert client.post("/api/chats/missing/ask", json={"question": "hi"}).status_code == 404


def test_busy_chat_refuses_a_second_question(client: TestClient) -> None:
    chat = new_chat(client)
    app_state(client).answering.add(chat)
    response = client.post(f"/api/chats/{chat}/ask", json={"question": "hi"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "busy"


# ---------------------------------------------------------------- settings


def test_settings_hide_secrets_and_save(client: TestClient, settings: Settings) -> None:
    body = client.put(
        "/api/settings", json={"changes": {"top_k": 5, "openai_key": "sk-secret"}}
    ).json()
    assert body["settings"]["top_k"] == 5
    assert body["settings"]["openai_key"] is True  # only whether it's set
    assert "sk-secret" not in json.dumps(body)
    assert "sk-secret" not in (settings.library / ".outlawz" / "settings.json").read_text()
    assert "sk-secret" in (settings.data_dir / "secrets.json").read_text()
    # An empty secret keeps the current one.
    client.put("/api/settings", json={"changes": {"openai_key": ""}})
    assert app_state(client).settings.openai_key == "sk-secret"
    bad = client.put("/api/settings", json={"changes": {"top_k": 0}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_setting"
    machine = client.put("/api/settings", json={"changes": {"port": 1}})
    assert machine.status_code == 400


def test_settings_survive_restart(settings: Settings) -> None:
    with make_client(settings) as c:
        c.put("/api/settings", json={"changes": {"temperature": 0.5}})
    from outlawz.config import load_settings

    reloaded = load_settings(library=settings.library, data_dir=settings.data_dir)
    assert reloaded.temperature == 0.5


def test_test_connection(client: TestClient) -> None:
    result = client.post("/api/settings/test", json={"changes": {}}).json()
    assert result["ok"] is True  # the fake model
    unreachable = client.post(
        "/api/settings/test",
        json={"changes": {"llm": "ollama", "ollama_url": "http://127.0.0.1:9"}},
    ).json()
    assert unreachable["ok"] is False and "isn't answering" in unreachable["message"]
    assert app_state(client).settings.llm == "fake"  # testing doesn't save anything
    assert client.post("/api/settings/test", json={"changes": {"top_k": 3}}).status_code == 422


def test_changing_the_search_model_reprocesses(
    live_client: TestClient, library: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import outlawz.state as state_module
    from outlawz.ingest.embed import FakeEmbedder

    class OtherFake(FakeEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.model_id = "fake:other"

    original_id = state_module.embedder_id
    monkeypatch.setattr(
        state_module,
        "embedder_id",
        lambda s: "fake:other" if s.embed_model == "other" else original_id(s),
    )
    monkeypatch.setattr(state_module, "make_embedder", lambda s: OtherFake())
    state = app_state(live_client)
    body = live_client.put("/api/settings", json={"changes": {"embed_model": "other"}}).json()
    assert body["notes"] and "prepared again" in body["notes"][0]
    wait_ingested(live_client)
    assert state.embedder is not None and state.embedder.model_id == "fake:other"
    docs = live_client.get("/api/documents").json()["documents"]
    assert {d["embed_model"] for d in docs} == {"fake:other"}
    assert {d["status"] for d in docs} == {"ready"}
    hits = live_client.post("/api/search", json={"query": "refund window"}).json()["hits"]
    assert hits and hits[0]["document"] == "refunds.pdf"
