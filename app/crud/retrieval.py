"""Data access and the budget policy (Rule 3) for topic-set retrieval.

A selected topic comes back complete, never as a top-k slice. Rules 0-2 live in app/chat/retrieval.py.
"""
import os
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.model import Crop, Item

# --- Rule 2: keep the top k topics ---
# fixed and tested now so the number is not invented later; override with TOPIC_SELECTION_K.
TOPIC_SELECTION_K = int(os.environ.get("TOPIC_SELECTION_K", "3"))

# --- Rule 3: the context budget, in characters ---
# not tokens: a real tokeniser is provider-specific, so estimate at 4 chars/token.
# See document_context_chars for what it does not count.
CHARS_PER_TOKEN = 4.0

# conservative default, in tokens; override with CONTEXT_TOKEN_BUDGET.
CONTEXT_TOKEN_BUDGET = int(os.environ.get("CONTEXT_TOKEN_BUDGET", "8000"))
CONTEXT_CHAR_BUDGET = int(CONTEXT_TOKEN_BUDGET * CHARS_PER_TOKEN)


def topic_set_statement(crop_id: int, topic: str) -> Select:
    """Every published document for one crop and one topic.

    No LIMIT (tests/test_retrieval.py checks the compiled and executed SQL); ordered by id for stability.
    """
    return (
        select(Item)
        .where(
            Item.crop_id == crop_id,
            # normalise the input, not the column: lower()/trim() on Item.topic would stop
            # PostgreSQL using ix_items_crop_id_topic.
            Item.topic == topic.strip().lower(),
            Item.published.is_(True),
        )
        .order_by(Item.id)
    )


async def get_topic_set(db: AsyncSession, crop_id: int, topic: str) -> list[Item]:
    result = await db.execute(topic_set_statement(crop_id, topic))
    return list(result.scalars().all())


async def get_crop_id_by_slug(db: AsyncSession, crop_slug: str) -> int | None:
    return await db.scalar(select(Crop.id).where(Crop.slug == crop_slug))


async def get_crop_slugs(db: AsyncSession, crop_ids: set[int]) -> dict[int, str]:
    rows = await db.execute(select(Crop.id, Crop.slug).where(Crop.id.in_(crop_ids)))
    return {crop_id: slug for crop_id, slug in rows}


def document_context_chars(documents: list[Item]) -> int:
    """Character count of one topic's context: titles and bodies only.

    Provenance is assumed to ride as citation metadata. If it goes into the prompt this
    undercounts ~2x (~3x for one-document ECOCROP topics); worst case at k=3 is ~40% of budget.
    """
    return sum(len(document.title) + len(document.body) for document in documents)


@dataclass
class TopicCandidate:
    """One topic's complete document set plus its selection score.

    The score is only compared here, so Rule 3 is testable with made-up scores.
    """

    topic: str
    score: float
    documents: list[Item]
    crop_id: int | None = None

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def context_chars(self) -> int:
        return document_context_chars(self.documents)


class TopicBudgetExceeded(Exception):
    """Rule 3: the one topic left still exceeds the budget alone; refuse, never truncate."""

    def __init__(self, topic: str, document_count: int, context_chars: int, budget: int):
        self.topic = topic
        self.document_count = document_count
        self.context_chars = context_chars
        self.budget = budget
        super().__init__(
            f"topic {topic!r} alone needs {context_chars} characters across "
            f"{document_count} documents, exceeding the {budget}-character budget"
        )


def assemble_within_budget(
    candidates: list[TopicCandidate],
    budget: int = CONTEXT_CHAR_BUDGET,
) -> tuple[list[TopicCandidate], list[TopicCandidate]]:
    """Rule 3: drop whole topics (lowest score first) while over budget and more than one
    remains; raise TopicBudgetExceeded if the last one alone still exceeds it.

    Returns (kept, dropped): kept highest score first, dropped in removal order.
    """
    ordered = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)

    kept = list(ordered)
    dropped: list[TopicCandidate] = []
    total = sum(candidate.context_chars for candidate in kept)
    while total > budget and len(kept) > 1:
        loser = kept.pop()  # ordered highest-first: the last scores lowest
        dropped.append(loser)
        total -= loser.context_chars

    if kept and kept[-1].context_chars > budget:
        offender = kept[-1]
        raise TopicBudgetExceeded(
            offender.topic, offender.document_count, offender.context_chars, budget,
        )

    return kept, dropped
