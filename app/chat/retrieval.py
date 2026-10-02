"""Vector topic selection: a question in, whole topics out.

Four rules, in order. Each works on **topics**; none ever filters or ranks the
documents inside one, because that is the winner-picking app/model/model.py
forbids for sources that disagree.

    Rule 0 -- abstain when no topic clears the relevance floor.
    Rule 1 -- score a topic by its best-matching chunk (MAX, not mean).
    Rule 2 -- keep the top k topics.
    Rule 3 -- fit the kept topics into the context budget
              (assemble_within_budget, reused unchanged).

Topics are *scored* from the `published_item_chunks` view. Each selected
topic's documents are then fetched by app/crud/retrieval.py's
`topic_set_statement` -- the same no-LIMIT query GET /retrieval uses -- because
the view carries no title or provenance, and this package may not name the
raw tables (tests/test_chat_layer_isolation.py). Importing it keeps one
definition of "a topic's complete published document set".

Functions take a synchronous Session. An async caller can reach them through
`AsyncSession.run_sync`.
"""
import os

from pgvector.sqlalchemy import Vector
from sqlalchemy import column, func, select, table
from sqlalchemy.orm import Session

from app.chat.embeddings import EMBEDDING_DIMENSIONS, Embedder
from app.crud.retrieval import (
    TOPIC_SELECTION_K,
    TopicCandidate,
    assemble_within_budget,
    topic_set_statement,
)

# Rule 0's floor, on raw cosine similarity (range -1..1).
#
# Ships dark. -1.0 is the one value that is a true no-op: cosine similarity
# cannot go below it. A floor of 0.0 would already drop a topic whose best
# chunk is slightly anti-correlated with the question. The real value cannot be
# chosen before real embeddings exist -- scripts/calibrate_floor.py measures
# it -- and the course's 0.4 does not port: it sits on LangChain's normalised
# [0,1] relevance scale, not raw cosine.
#
# Override with the TOPIC_SCORE_FLOOR env var (read at import time).
TOPIC_SCORE_FLOOR = float(os.environ.get("TOPIC_SCORE_FLOOR", "-1.0"))

# Only the columns this module reads; not a mapped class. Read through
# SQLAlchemy Core so no model is named here.
_view = table(
    "published_item_chunks",
    column("source_id"),
    column("crop_id"),
    column("topic"),
    column("content"),
    column("embedding", Vector(EMBEDDING_DIMENSIONS)),
)


class NoRelevantTopics(Exception):
    """Rule 0: nothing cleared the floor. Abstain; never answer."""


def score_topics(
    session: Session, query_vector: list[float], k: int = TOPIC_SELECTION_K
) -> list[tuple[int, str, float]]:
    """Rule 1 and the SQL half of Rule 2: (crop_id, topic, score) for the k
    best topics, best first.

    A topic's score is the MAX cosine similarity over its chunks. A mean would
    penalise topics holding many disagreeing sources -- perverse in a system
    built to surface them. LIMIT applies to topics, never to documents.
    Grouped by (crop_id, topic): a topic name is only unique within a crop.
    """
    similarity = 1 - _view.c.embedding.cosine_distance(query_vector)
    score = func.max(similarity).label("score")
    statement = (
        select(_view.c.crop_id, _view.c.topic, score)
        .where(_view.c.topic.is_not(None))
        .group_by(_view.c.crop_id, _view.c.topic)
        .order_by(score.desc(), _view.c.topic)
        .limit(k)
    )
    return [(row.crop_id, row.topic, float(row.score)) for row in session.execute(statement)]


def fetch_candidates(
    session: Session, scored: list[tuple[int, str, float]]
) -> list[TopicCandidate]:
    """Each topic's complete published document set -- a second query per
    topic, no LIMIT."""
    return [
        TopicCandidate(
            topic=topic,
            score=score,
            documents=list(session.execute(topic_set_statement(crop_id, topic)).scalars().all()),
        )
        for crop_id, topic, score in scored
    ]


def select_topics(
    candidates: list[TopicCandidate],
    k: int = TOPIC_SELECTION_K,
    floor: float = TOPIC_SCORE_FLOOR,
) -> list[TopicCandidate]:
    relevant = [c for c in candidates if c.score >= floor]  # Rule 0: whole topics only
    if not relevant:
        raise NoRelevantTopics(
            f"no topic scored at or above the floor {floor} "
            f"(best of {len(candidates)}: "
            f"{max((c.score for c in candidates), default=None)})"
        )
    return sorted(relevant, key=lambda c: c.score, reverse=True)[:k]  # Rule 2


def retrieve_topics(
    session: Session,
    question: str,
    embedder: Embedder,
    k: int = TOPIC_SELECTION_K,
    floor: float = TOPIC_SCORE_FLOOR,
) -> tuple[list[TopicCandidate], list[TopicCandidate]]:
    """Rules 0-3 end to end. Returns (kept, dropped_for_budget).

    Raises NoRelevantTopics (Rule 0) or TopicBudgetExceeded (Rule 3).
    """
    (query_vector,) = embedder.embed([question])
    candidates = fetch_candidates(session, score_topics(session, query_vector, k))
    return assemble_within_budget(select_topics(candidates, k, floor))
