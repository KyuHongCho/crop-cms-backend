"""POST /chat request and response shapes.

HTTP 200 with `abstained` as discriminator: an unanswerable question is not an error. The
errors are the context budget (413) and an unusable model service (503).
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
    crop_slug: str | None = None  # only when the question did not fix the crop


class DroppedChatTopic(DroppedTopic):
    crop_slug: str | None = None  # only when the question did not fix the crop


class Abstention(BaseModel):
    reason: str  # "out_of_scope" (no retrieval) or "no_relevant_topics"


class ChatResponse(BaseModel):
    answer: str
    # provenance comes from the retrieved set, never model output (stored values, verbatim).
    documents: list[CitedDocument] = []
    topics_used: list[str] = []
    # parallel to topics_used: each topic's crop; empty when the question fixed the crop.
    topics_used_crops: list[str] = []
    dropped: list[DroppedChatTopic] = []
    abstained: Abstention | None = None
    truncated: bool = False  # cut at the generator's max_tokens, so possibly incomplete
