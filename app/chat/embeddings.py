"""The embedder seam: everything needing a vector takes an `Embedder`.

`get_embedder()` picks `openai` (default) or `fake` from $EMBEDDER; tests use `fake`, so no key.
"""
import hashlib
import math
import os
import random
from typing import Protocol

# the column is vector(1536) (app/model/model.py); every embedder must match it.
EMBEDDING_DIMENSIONS = 1536

OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
FAKE_EMBEDDING_MODEL = "fake-sha256-1536"


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """text-embedding-3-small via langchain-openai. Constructing it without OPENAI_API_KEY
    raises before anything is sent."""

    model = OPENAI_EMBEDDING_MODEL

    def __init__(self) -> None:
        # imported here so the fake path never loads the provider SDK.
        from langchain_openai import OpenAIEmbeddings

        self._client = OpenAIEmbeddings(
            model=OPENAI_EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)


class FakeEmbedder:
    """Deterministic and offline: seeded from the sha256 of the text and scaled to unit length,
    so cosine similarity between vectors is meaningful."""

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
    """`name`, else $EMBEDDER, else "openai". Unknown names raise so a typo cannot silently
    select the paid provider. Returns the class unbuilt, so `.model` is readable without a key."""
    name = name or os.environ.get("EMBEDDER", "openai")
    if name not in _EMBEDDERS:
        raise ValueError(f"unknown EMBEDDER {name!r}; expected one of {sorted(_EMBEDDERS)}")
    return _EMBEDDERS[name]


def get_embedder(name: str | None = None) -> Embedder:
    return embedder_class(name)()
