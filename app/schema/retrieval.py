"""Response shapes for topic-set retrieval.

There is deliberately **no request shape and no `limit` field** anywhere in
this module. The endpoint takes a crop and a topic and returns the whole set:
see `app/model/model.py:92-94` -- "retrieval returns every document sharing a
`topic` rather than a top-k slice -- otherwise a LIMIT silently picks a winner
among disagreeing sources." A `limit` a caller could opt into is still a way to
pick that winner, so it does not exist to be opted into.

`RetrievedDocument` deliberately does not reuse `app/schema/item.py`'s
`ItemResponse`. That one is the CMS's authoring view and carries
`sub_category_id` -- filing metadata, which the retrieval consumer has no use
for and which is not provenance. The overlap is not accidental duplication:
the two shapes answer to different callers and are free to diverge.
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

    # --- provenance, mirroring crop_advisor/claims.py:40-47 -----------------
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

    # Budget accounting -- see app/crud/retrieval.py for the ratio's rationale.
    context_chars: int
    context_char_budget: int
    chars_per_token: float
    dropped: list[DroppedTopic] = []


class BudgetRefusal(BaseModel):
    """Rule 3 clause 2: refuse only when a single topic alone exceeds the budget.

    Carried as the `detail` of an HTTP 413. Naming the topic and its document
    count is the whole point -- the refusal has to be more useful than a
    truncation would have been, or dropping the truncation gains nothing.
    """

    reason: Literal["topic_alone_exceeds_context_budget"] = (
        "topic_alone_exceeds_context_budget"
    )
    topic: str
    document_count: int
    context_chars: int
    context_char_budget: int
    message: str
