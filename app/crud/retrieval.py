"""Data access and the budget policy for topic-set retrieval.

The centrepiece rule -- see `app/model/model.py:92-94` -- is that retrieval
never picks a winner among documents disagreeing about the same topic: a
selected topic returns **complete**, never a top-k slice.

Three rules, decided here (N3 in plan-1). Only rule 3's machinery is built and
tested in this slice; rules 1 and 2 have no chunk/embedding table to operate
on until Slice 5, so they are recorded as named, tested constants/contracts
here and wired to real scoring in Slice 6.

    Rule 1 -- topic score = MAX chunk similarity.  Decided here, built Slice 6.
    Rule 2 -- k = 3 topics.                        Decided here, built Slice 6.
    Rule 3 -- budget policy (below).               Decided AND built here.
"""
import os
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.model import Crop, Item

# --- Rule 2: k = 3 topics -----------------------------------------------------
#
# Decided in this slice, per plan-1:137/142. Not exercised until Slice 6 builds
# topic selection -- there is no scoring machinery here to select *from* -- but
# named and asserted now so the number is not invented later.
# Configurable via env var so a later slice can tune it without a code change.
TOPIC_SELECTION_K = int(os.environ.get("TOPIC_SELECTION_K", "3"))

# --- Rule 3: budget policy, measured in characters ----------------------------
#
# A character count with a documented characters-per-token ratio, not a
# tokeniser -- plan-1:171-180 defers the provider decision to immediately
# after this slice, so no tokeniser is importable yet, and a tokeniser would
# be provider-specific regardless. 4 characters per token is the commonly
# cited rule of thumb for English prose (matches OpenAI's own documented
# approximation). Erring approximate is fine here because it errs toward
# dropping early, never toward silently overrunning a real budget.
CHARS_PER_TOKEN = 4.0

# A conservative default context budget in tokens, translated to characters
# below. Configurable via env var for the same reason as TOPIC_SELECTION_K.
CONTEXT_TOKEN_BUDGET = int(os.environ.get("CONTEXT_TOKEN_BUDGET", "8000"))
CONTEXT_CHAR_BUDGET = int(CONTEXT_TOKEN_BUDGET * CHARS_PER_TOKEN)


def topic_set_statement(crop_id: int, topic: str) -> Select:
    """The complete published document set for one crop and one topic.

    No LIMIT, no relevance ORDER BY -- ordered by id only, for a stable
    response shape. This is the exact statement the no-truncation guarantee
    rests on: tests/test_retrieval.py compiles and executes this statement
    directly and asserts LIMIT never appears in either form.
    """
    return (
        select(Item)
        .where(
            Item.crop_id == crop_id,
            Item.topic == topic,
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
    """Character count of one topic's assembled context.

    Title + body only -- the two fields actually assembled into a prompt.
    Provenance (source, reference, url, ...) is metadata reported alongside
    the answer, not counted against the budget.
    """
    return sum(len(document.title) + len(document.body) for document in documents)


@dataclass
class TopicCandidate:
    """One topic's complete document set plus its selection score.

    The score is opaque to this module -- it is only ever compared and
    ordered, never computed here. That is what lets Rule 3 be built and
    tested in this slice with constructed scores, and reused unchanged once
    Slice 6 supplies real MAX chunk-similarity scores (Rule 1).
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
    """Rule 3 clause 2: a single topic alone exceeds the budget.

    Dropping every other topic would not help -- this one topic's own
    context is already too large -- so this is refused rather than degraded.
    """

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
    """Rule 3: degrade by whole topics, refuse only as a last resort.

    1. Any candidate whose OWN context alone exceeds the budget raises
       TopicBudgetExceeded immediately -- naming it -- regardless of what else
       is being assembled: dropping every other topic could not make that one
       fit, so refusing is the only option left (clause 2).
    2. Otherwise, topics are dropped whole, lowest-score-first, until the
       assembled combination fits the budget (clause 1). Every dropped topic
       is returned, named, never partially truncated.

    Returns (kept, dropped), both ordered highest-score-first.
    """
    ordered = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)

    for candidate in ordered:
        if candidate.context_chars > budget:
            raise TopicBudgetExceeded(
                candidate.topic, candidate.document_count, candidate.context_chars, budget,
            )

    kept = list(ordered)
    dropped: list[TopicCandidate] = []
    total = sum(candidate.context_chars for candidate in kept)
    while total > budget and kept:
        loser = kept.pop()  # ordered highest-first -- the last entry scores lowest
        dropped.append(loser)
        total -= loser.context_chars

    return kept, dropped
