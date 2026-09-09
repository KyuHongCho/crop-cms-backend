"""Data access and the budget policy for topic-set retrieval.

The centrepiece rule -- see `app/model/model.py:92-94` -- is that retrieval
never picks a winner among documents disagreeing about the same topic: a
selected topic returns **complete**, never a top-k slice.

Three rules, decided here (N3 in plan-1). Only rule 3's machinery is built and
tested now; rules 1 and 2 have no chunk/embedding table to operate on until
that infrastructure exists, so they are recorded as named, tested
constants/contracts here and wired to real scoring once it does.

    Rule 1 -- topic score = MAX chunk similarity.  Decided here, built later.
    Rule 2 -- k = 3 topics.                        Decided here, built later.
    Rule 3 -- budget policy (below).               Decided AND built here.
"""
import os
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model.model import Crop, Item

# --- Rule 2: k = 3 topics -----------------------------------------------------
#
# Decided here, per plan-1:137/142. Not exercised until topic selection is
# built -- there is no scoring machinery here to select *from* -- but named
# and asserted now so the number is not invented later.
# Configurable via env var so a future change can tune it without a code edit.
TOPIC_SELECTION_K = int(os.environ.get("TOPIC_SELECTION_K", "3"))

# --- Rule 3: budget policy, measured in characters ----------------------------
#
# A character count with a documented characters-per-token ratio, not a
# tokeniser -- plan-1:171-180 defers the provider decision to immediately
# after this point, so no tokeniser is importable yet, and a tokeniser would
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
            # Normalize the caller's input, not the indexed column -- wrapping
            # Item.topic itself (e.g. func.lower(func.trim(Item.topic))) would
            # defeat ix_items_crop_id_topic (verified via EXPLAIN: the topic
            # half of the predicate falls back to a Filter/Recheck instead of
            # an Index Cond). Item.topic itself is normalized on write by
            # Item._normalize_topic (model.py), so the stored value is already
            # comparable to this.
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
    """Character count of one topic's assembled context.

    Title + body only. Provenance is currently assumed to never be inserted
    into the LLM prompt itself (it would be attached as structured citation
    metadata instead) -- but that is a design decision the chat feature
    hasn't made yet, not a settled fact. If it ends up injecting provenance
    text into the prompt, this undercounts the real context by roughly 1.4x-2x
    (measured
    across the current seed corpus) and must be revisited then.
    """
    return sum(len(document.title) + len(document.body) for document in documents)


@dataclass
class TopicCandidate:
    """One topic's complete document set plus its selection score.

    The score is opaque to this module -- it is only ever compared and
    ordered, never computed here. That is what lets Rule 3 be built and
    tested now with constructed scores, and reused unchanged once real MAX
    chunk-similarity scores (Rule 1) exist.
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

    1. Topics are dropped whole, lowest-score-first, until the assembled
       combination fits the budget (clause 1). Every dropped topic is
       returned, named, never partially truncated.
    2. Only once dropping can go no further (a single candidate remains, or
       dropping already emptied the set) is what is left checked against the
       budget: if that survivor's OWN context alone still exceeds it,
       TopicBudgetExceeded is raised naming it -- dropping every OTHER topic
       already happened and did not help, so refusing is the only option
       left. An oversized topic that clause 1 would have dropped anyway
       (because it scored lowest) is never refused on its own -- only the
       one still standing after dropping is ever blamed.

    Returns (kept, dropped) -- kept highest-score-first, dropped lowest-score-first
    (the order they were discarded in).
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
