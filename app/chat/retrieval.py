"""Vector topic selection: a question in, whole topics out.

Rules 0-3 work on topics, never ranking documents inside one. Scored from the `published_item_chunks`
view; documents come via topic_set_statement (the view lacks provenance; no raw tables here).
"""
import os

from pgvector.sqlalchemy import Vector
from sqlalchemy import column, func, select, table, true
from sqlalchemy.orm import Session

from app.chat.embeddings import EMBEDDING_DIMENSIONS, Embedder
from app.crud.retrieval import (
    TOPIC_SELECTION_K,
    TopicCandidate,
    assemble_within_budget,
    topic_set_statement,
)

# Rule 0's floor on raw cosine similarity. Ships dark at -1.0, the only true no-op (0.0 would
# drop anti-correlated topics); scripts/calibrate_floor.py measures the real value (the course's
# 0.4 is on a normalised scale). Override with TOPIC_SCORE_FLOOR (read at import).
TOPIC_SCORE_FLOOR = float(os.environ.get("TOPIC_SCORE_FLOOR", "-1.0"))

# only the columns read here, via Core so no model is named.
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
    session: Session,
    query_vector: list[float],
    k: int = TOPIC_SELECTION_K,
    crop_id: int | None = None,
) -> list[tuple[int, str, float]]:
    """Rule 1 and the SQL half of Rule 2: (crop_id, topic, score) for the k best topics.

    Score is MAX similarity over chunks (a mean would penalise topics with many disagreeing
    sources). LIMIT applies to topics, never documents. Without `crop_id`, every crop competes.
    """
    similarity = 1 - _view.c.embedding.cosine_distance(query_vector)
    score = func.max(similarity).label("score")
    statement = (
        select(_view.c.crop_id, _view.c.topic, score)
        .where(_view.c.topic.is_not(None))
        .where(_view.c.crop_id == crop_id if crop_id is not None else true())
        .group_by(_view.c.crop_id, _view.c.topic)
        .order_by(score.desc(), _view.c.topic)
        .limit(k)
    )
    return [(row.crop_id, row.topic, float(row.score)) for row in session.execute(statement)]


def fetch_candidates(
    session: Session, scored: list[tuple[int, str, float]]
) -> list[TopicCandidate]:
    """Each topic's complete published document set; a second query per topic, no LIMIT."""
    return [
        TopicCandidate(
            topic=topic,
            score=score,
            documents=list(session.execute(topic_set_statement(crop_id, topic)).scalars().all()),
            crop_id=crop_id,
        )
        for crop_id, topic, score in scored
    ]


def select_topics(
    candidates: list[TopicCandidate],
    k: int = TOPIC_SELECTION_K,
    floor: float = TOPIC_SCORE_FLOOR,
) -> list[TopicCandidate]:
    relevant = [c for c in candidates if c.score >= floor]  # Rule 0
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
    crop_id: int | None = None,
) -> tuple[list[TopicCandidate], list[TopicCandidate]]:
    """Rules 0-3 end to end: (kept, dropped_for_budget). Raises NoRelevantTopics or TopicBudgetExceeded."""
    (query_vector,) = embedder.embed([question])
    candidates = fetch_candidates(session, score_topics(session, query_vector, k, crop_id))
    return assemble_within_budget(select_topics(candidates, k, floor))
