"""Response shapes for topic-set retrieval.

No request shape or `limit` field on purpose: a caller-chosen limit could pick a winner among
disagreeing sources. RetrievedDocument stays separate from item.py's ItemResponse (authoring view).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RetrievedDocument(BaseModel):
    """One document as retrieval hands it on: prose plus its provenance.

    Every provenance field of `Item` is here, none is optional-by-omission.
    A retrieved document that arrived without its source would be exactly the
    silent-winner problem in another costume -- the reader could not tell which
    of three disagreeing claims they were looking at.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    topic: str | None = None
    title: str
    body: str

    # provenance, modelled on crop_advisor/claims.py's Claim
    source: str
    reference: str
    url: str
    read_directly: bool
    via: str | None = None
    condition: str | None = None
    licence_note: str | None = None


class DroppedTopic(BaseModel):
    """A whole topic dropped to fit the budget -- named, never silently."""

    topic: str
    score: float
    document_count: int
    context_chars: int


class TopicSetResponse(BaseModel):
    """The complete published document set for one crop and one topic.

    `document_count` is not redundant beside `len(documents)`: it is the
    assertion this endpoint exists to make. A client reading only the count
    still sees a truncation, because a truncated list would have to disagree
    with it -- and nothing in this codebase writes the count from anything but
    the list it ships with.

    The budget figures are reported on every successful response, not only on
    a refusal, so the margin is observable *before* a topic grows past it.
    """

    crop_slug: str
    topic: str
    document_count: int
    documents: list[RetrievedDocument]

    # budget accounting; see app/crud/retrieval.py
    context_chars: int
    context_char_budget: int
    chars_per_token: float
    dropped: list[DroppedTopic] = []


class BudgetRefusal(BaseModel):
    """Refusal (HTTP 413 detail) only when a single topic alone exceeds the budget.

    Names the topic and document count so the refusal beats a silent truncation.
    """

    reason: Literal["topic_alone_exceeds_context_budget"] = (
        "topic_alone_exceeds_context_budget"
    )
    topic: str
    document_count: int
    context_chars: int
    context_char_budget: int
    message: str
