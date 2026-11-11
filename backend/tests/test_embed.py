from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from outlawz.errors import TransientError
from outlawz.ingest.embed import FakeEmbedder, OllamaEmbedder, normalise


def test_fake_embedder_is_deterministic_and_normalised() -> None:
    a = FakeEmbedder()
    b = FakeEmbedder()
    texts = ["refund policy for sales", "shipping times abroad"]
    va, vb = a.embed_passages(texts), b.embed_passages(texts)
    assert np.array_equal(va, vb)
    assert np.allclose(np.linalg.norm(va, axis=1), 1.0)
    q = a.embed_query("refund policy")
    assert float(va[0] @ q) > float(va[1] @ q)  # shared words → more similar


def test_normalise_handles_zero_rows() -> None:
    out = normalise(np.array([[0.0, 0.0], [3.0, 4.0]]))
    assert np.allclose(out[1], [0.6, 0.8]) and np.allclose(out[0], [0, 0])


def test_ollama_embedder_parses_response() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in body["input"]]})

    emb = OllamaEmbedder(
        "http://ollama:11434",
        "nomic-embed-text",
        transport=httpx.MockTransport(handler),
        batch_size=2,
    )
    vectors = emb.embed_passages(["a", "b", "c"])
    assert vectors.shape == (3, 2) and np.allclose(vectors[0], [0.6, 0.8])
    assert [len(b["input"]) for b in seen] == [2, 1]  # type: ignore[arg-type]
    assert emb.model_id == "ollama:nomic-embed-text"


def test_ollama_missing_model_message() -> None:
    emb = OllamaEmbedder(
        "http://ollama:11434",
        "nomic-embed-text",
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
    )
    with pytest.raises(TransientError) as info:
        emb.embed_query("x")
    assert "ollama pull nomic-embed-text" in str(info.value)


def test_ollama_unreachable_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    emb = OllamaEmbedder("http://ollama:11434", "m", transport=httpx.MockTransport(handler))
    with pytest.raises(TransientError):
        emb.embed_query("x")
