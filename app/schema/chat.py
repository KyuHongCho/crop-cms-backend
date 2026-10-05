"""POST /chat request and response shapes.

HTTP 200 throughout, with `abstained` as the discriminator: a question that
retrieval cannot answer is not an HTTP error (the same convention as an empty
topic set in GET /retrieval). The refusals that are errors are the context
budget (413) and an unusable model service (503).
"""
from pydantic import BaseModel, Field, field_validator

from app.schema.retrieval import DroppedTopic, RetrievedDocument


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class CitedDocument(RetrievedDocument):
    """A RetrievedDocument plus the key the answer cites it by ("S1")."""

    key: str
    crop_slug: str | None = None  # set only when the question did not fix the crop


class DroppedChatTopic(DroppedTopic):
    crop_slug: str | None = None  # set only when the question did not fix the crop


class Abstention(BaseModel):
    reason: str  # "out_of_scope" (declined, no retrieval) or "no_relevant_topics"


class ChatResponse(BaseModel):
    answer: str
    # Provenance is rendered from the retrieved set, never from model output:
    # `reference`, `url` and `licence_note` are the stored values, verbatim.
    documents: list[CitedDocument] = []
    topics_used: list[str] = []
    # Parallel to topics_used (same order and same length when unscoped): the crop of each topic. Empty when
    # the question fixed the crop, so a shared topic name stays distinguishable.
    topics_used_crops: list[str] = []
    dropped: list[DroppedChatTopic] = []
    abstained: Abstention | None = None  # None on a normal answer
