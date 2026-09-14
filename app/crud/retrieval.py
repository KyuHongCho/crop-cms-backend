"""Data access and the budget policy for topic-set retrieval.

Retrieval never picks a winner among documents that disagree on the same topic:
a selected topic comes back complete, never as a top-k slice.

Three rules govern topic selection:

    Rule 1 -- score a topic by its best-matching chunk.     Not built yet.
    Rule 2 -- keep the top k topics (3 by default).         Only the constant exists.
    Rule 3 -- fit the kept topics into the context budget.  Built and tested.

Rules 1 and 2 need embeddings, which do not exist yet.
"""
import os
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.model import Crop, Item

# --- Rule 2: keep the top k topics --------------------------------------------
#
# Unused until topic selection exists, but fixed and tested now so the number
# is not invented later. Override with the TOPIC_SELECTION_K env var.
TOPIC_SELECTION_K = int(os.environ.get("TOPIC_SELECTION_K", "3"))

# --- Rule 3: the context budget, measured in characters -----------------------
#
# Characters, not tokens: there is no tokeniser yet because the model provider
# is not chosen, and a tokeniser would be provider-specific anyway. 4 characters
# per token is a common rule of thumb for English prose. It is an estimate, not
# a guarantee -- see document_context_chars below for what it does not count.
CHARS_PER_TOKEN = 4.0

# A conservative default budget in tokens, converted to characters below.
# Override with the CONTEXT_TOKEN_BUDGET env var.
CONTEXT_TOKEN_BUDGET = int(os.environ.get("CONTEXT_TOKEN_BUDGET", "8000"))
CONTEXT_CHAR_BUDGET = int(CONTEXT_TOKEN_BUDGET * CHARS_PER_TOKEN)


def topic_set_statement(crop_id: int, topic: str) -> Select:
    """Every published document for one crop and one topic.

    No LIMIT, and ordered by id only so the order is stable. tests/test_retrieval.py
    checks for LIMIT in both the compiled statement and the SQL actually executed.
    """
    return (
        select(Item)
        .where(
            Item.crop_id == crop_id,
            # Normalize the input, not the column: wrapping Item.topic in
            # lower()/trim() would stop PostgreSQL using the
            # ix_items_crop_id_topic index for it. Topics written through the
            # ORM are already normalized (Item._normalize_topic in model.py).
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


def document_context_chars(documents: list[Item]) -> int:
    """Character count of one topic's context: titles and bodies only.

    Provenance (source, reference, URL, ...) is not counted, on the assumption
    it will be attached as citation metadata rather than put into the prompt.
    The chat feature has not decided that yet. If provenance does go into the
    prompt, this undercounts by roughly 1.4x-2x (measured on the seed corpus)
    and must be revisited.
    """
    return sum(len(document.title) + len(document.body) for document in documents)


@dataclass
class TopicCandidate:
    """One topic's complete document set plus its selection score.

    The score is only compared here, never computed, so Rule 3 can be tested
    now with made-up scores and reused unchanged once real scores (Rule 1) exist.
    """

    topic: str
    score: float
    documents: list[Item]

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def context_chars(self) -> int:
        return document_context_chars(self.documents)


class TopicBudgetExceeded(Exception):
    """Rule 3: the one topic left, after any dropping, still exceeds the
    budget on its own, so the request is refused rather than truncated."""

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
    """Rule 3: drop whole topics to fit the budget; refuse only as a last resort.

    1. While the kept topics exceed the budget and more than one is left, drop
       the lowest-scoring topic -- whole, never part of it. Dropped topics are
       returned so the response can name them.
    2. If the one topic left still exceeds the budget on its own, raise
       TopicBudgetExceeded naming it. When other topics remain, an oversized
       lowest-scoring topic is dropped by step 1 rather than refused.

    Returns (kept, dropped): kept highest score first, dropped in the order
    they were removed (lowest score first).
    """
    ordered = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)

    kept = list(ordered)
    dropped: list[TopicCandidate] = []
    total = sum(candidate.context_chars for candidate in kept)
    while total > budget and len(kept) > 1:
        loser = kept.pop()  # ordered highest-first -- the last entry scores lowest
        dropped.append(loser)
        total -= loser.context_chars

    if kept and kept[-1].context_chars > budget:
        offender = kept[-1]
        raise TopicBudgetExceeded(
            offender.topic, offender.document_count, offender.context_chars, budget,
        )

    return kept, dropped
