"""Prints on-topic vs off-topic topic-score distributions, to choose TOPIC_SCORE_FLOOR.

    docker compose exec -T cms python -m scripts.calibrate_floor

Needs a corpus embedded with the real embedder (OPENAI_API_KEY, `scripts.reindex`).
Under EMBEDDER=fake the scores are noise and the output means nothing.

For each labelled question below, scores every topic (Rule 1: best chunk) and
splits the scores into the topic the question is about (on-topic) and the rest
(off-topic). A good floor sits between the highest off-topic score and the
lowest on-topic score; if the ranges overlap, no floor separates them and that
is itself the finding.

Edit QUESTIONS to match the corpus. The recorded output belongs in
docs/design-notes.md, beside the constant.
"""
import statistics
import sys

from sqlalchemy.orm import Session

from app.chat.embeddings import get_embedder
from app.chat.retrieval import score_topics
from app.db.migrate_db import engine as sync_engine

# (question, the topic it is about)
QUESTIONS = [
    ("how hot should basil be?", "optimal-temperature"),
    ("what temperature does basil grow best at?", "optimal-temperature"),
    ("how often should I water basil?", "watering-needs"),
    ("what soil pH does basil want?", "soil-ph"),
    ("what pests attack basil?", "pest-and-disease"),
    ("how do I propagate basil from cuttings?", "propagation"),
]


def summarise(label: str, scores: list[float], out=print) -> None:
    if not scores:
        out(f"{label}: no scores")
        return
    out(f"{label}: n={len(scores)} min={min(scores):.4f} "
        f"median={statistics.median(scores):.4f} max={max(scores):.4f}")


def main() -> None:
    sync_engine.echo = False
    embedder = get_embedder()
    on_topic: list[float] = []
    off_topic: list[float] = []
    with Session(sync_engine) as session:
        for question, topic in QUESTIONS:
            (vector,) = embedder.embed([question])
            for _crop_id, name, score in score_topics(session, vector, k=1000):
                (on_topic if name == topic else off_topic).append(score)
    print(f"embedder: {embedder.model}")
    summarise("on-topic ", on_topic)
    summarise("off-topic", off_topic)
    if on_topic and off_topic:
        gap = min(on_topic) - max(off_topic)
        print(f"gap (lowest on-topic - highest off-topic): {gap:.4f}"
              + ("  -> a floor between them separates the two" if gap > 0 else "  -> OVERLAP: no floor separates them"))


if __name__ == "__main__":
    sys.exit(main())
