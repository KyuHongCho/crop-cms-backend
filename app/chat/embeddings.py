"""The embedder seam.

Everything that needs a vector takes an `Embedder`, so the real provider and
the offline fake are interchangeable. `get_embedder()` picks one from the
`EMBEDDER` environment variable: `openai` (the default) or `fake`. The test
suite uses `fake` and so never needs an API key.
"""
import hashlib
import math
import os
import random
from typing import Protocol

# The column is vector(1536) (app/model/model.py); every embedder must match it.
EMBEDDING_DIMENSIONS = 1536

OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
FAKE_EMBEDDING_MODEL = "fake-sha256-1536"


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """text-embedding-3-small via langchain-openai. Reads OPENAI_API_KEY from
    the environment; constructing it without one (unset or empty) raises,
    before anything is sent."""

    model = OPENAI_EMBEDDING_MODEL

    def __init__(self) -> None:
        # Imported here, not at module level, so the fake path never loads
        # the provider SDK.
        from langchain_openai import OpenAIEmbeddings

        self._client = OpenAIEmbeddings(
            model=OPENAI_EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)


class FakeEmbedder:
    """Deterministic and offline: each vector is seeded from the sha256 of
    its text, so the same text always gets the same vector, and is scaled to
    unit length, so cosine similarity between two of them is meaningful."""

    model = FAKE_EMBEDDING_MODEL

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest(), "big")
        rng = random.Random(seed)
        raw = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIMENSIONS)]
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]


_EMBEDDERS = {"openai": OpenAIEmbedder, "fake": FakeEmbedder}


def embedder_class(name: str | None = None) -> type:
    """`name`, else $EMBEDDER, else "openai". An unknown name raises rather
    than falling back, so a typo cannot silently select the paid provider.
    Returns the class without constructing it, so a caller can read `.model`
    without needing a key."""
    name = name or os.environ.get("EMBEDDER", "openai")
    if name not in _EMBEDDERS:
        raise ValueError(f"unknown EMBEDDER {name!r}; expected one of {sorted(_EMBEDDERS)}")
    return _EMBEDDERS[name]


def get_embedder(name: str | None = None) -> Embedder:
    return embedder_class(name)()
