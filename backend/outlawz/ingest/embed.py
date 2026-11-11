"""Embedding providers. Every vector is float32 and L2-normalised, so cosine similarity is a dot
product.

To add a provider: write a class with ``model_id``, ``embed_passages`` and ``embed_query``, add
it to ``make_embedder``, and add a test (``httpx.MockTransport`` for HTTP providers).
"""

from __future__ import annotations

import hashlib
import itertools
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np

from outlawz.config import Settings
from outlawz.errors import TransientError
from outlawz.search.tokenizer import tokenize

# done, total
EmbedProgress = Callable[[int, int], None]


class Embedder(Protocol):
    model_id: str  # "provider:model", stored per document

    def embed_passages(
        self, texts: list[str], progress: EmbedProgress | None = None
    ) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def normalise(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        norm = float(np.linalg.norm(matrix))
        return matrix / norm if norm > 0 else matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class FastEmbedder:
    """Local ONNX embeddings on the CPU via fastembed. The model downloads once to the OS data
    folder; after that everything runs offline."""

    def __init__(self, model: str, cache_dir: Path, batch_size: int = 32) -> None:
        self.model = model
        self.model_id = f"fastembed:{model}"
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self._engine: object | None = None
        self._lock = threading.Lock()

    def _load(self) -> object:
        with self._lock:
            if self._engine is None:
                try:
                    from fastembed import TextEmbedding

                    self._engine = TextEmbedding(
                        model_name=self.model, cache_dir=str(self.cache_dir)
                    )
                except Exception as exc:
                    raise TransientError(
                        f"The search model “{self.model}” couldn't be loaded. The first start "
                        f"needs an internet connection to download it (about 70 MB); after "
                        f"that Outlawz works offline."
                    ) from exc
            return self._engine

    def embed_passages(self, texts: list[str], progress: EmbedProgress | None = None) -> np.ndarray:
        engine = self._load()
        vectors: list[np.ndarray] = []
        total = len(texts)
        for start in range(0, total, self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(engine.passage_embed(batch, batch_size=self.batch_size))  # type: ignore[attr-defined]
            if progress:
                progress(min(start + len(batch), total), total)
        if not vectors:
            return np.zeros((0, 0), dtype=np.float32)
        return normalise(np.vstack(vectors))

    def embed_query(self, text: str) -> np.ndarray:
        engine = self._load()
        vector = next(iter(engine.query_embed(text)))  # type: ignore[attr-defined]
        return normalise(np.asarray(vector))


class OllamaEmbedder:
    """Embeddings from a local Ollama server (``/api/embed``)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        batch_size: int = 32,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_id = f"ollama:{model}"
        self.batch_size = batch_size
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def _embed(self, texts: list[str]) -> np.ndarray:
        try:
            response = self._client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": texts}
            )
        except httpx.HTTPError as exc:
            raise TransientError("Ollama isn't answering. Is it running?") from exc
        if response.status_code == 404:
            raise TransientError(
                f"Ollama doesn't have the model “{self.model}”. Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            raise TransientError(f"Ollama refused the request ({response.status_code}).")
        data = response.json()
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise TransientError("Ollama returned an unexpected answer.")
        return normalise(np.asarray(embeddings, dtype=np.float32))

    def embed_passages(self, texts: list[str], progress: EmbedProgress | None = None) -> np.ndarray:
        parts = []
        for start in range(0, len(texts), self.batch_size):
            parts.append(self._embed(texts[start : start + self.batch_size]))
            if progress:
                progress(min(start + self.batch_size, len(texts)), len(texts))
        return np.vstack(parts) if parts else np.zeros((0, 0), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]


class FakeEmbedder:
    """Deterministic, offline test double: hashed bag of words and word pairs. Texts that share
    words get similar vectors, which is enough to test ranking logic."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model_id = f"fake:hash-{dim}"

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        tokens = tokenize(text)
        features = tokens + [f"{a} {b}" for a, b in itertools.pairwise(tokens)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (0.5 if " " in feature else 1.0)
        return vector

    def embed_passages(self, texts: list[str], progress: EmbedProgress | None = None) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        matrix = np.vstack([self._vector(t) for t in texts])
        if progress:
            progress(len(texts), len(texts))
        return normalise(matrix)

    def embed_query(self, text: str) -> np.ndarray:
        return normalise(self._vector(text))


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "fake":
        return FakeEmbedder()
    if settings.embedder == "ollama":
        return OllamaEmbedder(settings.ollama_url, settings.embed_model, settings.llm_timeout)
    return FastEmbedder(settings.embed_model, settings.data_paths.models_dir)


def embedder_id(settings: Settings) -> str:
    if settings.embedder == "fake":
        return FakeEmbedder().model_id
    return f"{settings.embedder}:{settings.embed_model}"
