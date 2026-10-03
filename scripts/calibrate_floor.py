"""Prints on-topic vs off-topic topic-score distributions, to choose TOPIC_SCORE_FLOOR.

    docker compose exec -T cms python -m scripts.calibrate_floor

Needs a corpus embedded with the real embedder (OPENAI_API_KEY, `scripts.reindex`).
Under EMBEDDER=fake the scores are noise and the output means nothing.

For each labelled question below, scores the topics of the crop it names (Rule 1: best
chunk) and splits the scores into the topic the question is about (on-topic) and the
rest (off-topic). Scoring is per crop, as POST /chat will do, so another crop's topic
with the same name is never counted as on-topic. A good floor sits between the highest off-topic score and the
lowest on-topic score; if the ranges overlap, no floor separates them and that
is itself the finding.

Edit QUESTIONS to match the corpus. The recorded output belongs in
docs/design-notes.md, beside the constant.

score_topics keeps only the best k topics per question, dropping the lowest scores first, and
the gap below is derived from those scores. The script asks for TOPIC_CAP + 1: getting the extra
topic back proves some were dropped, and it stops instead of printing a gap it cannot trust.
Raise TOPIC_CAP if that happens.
"""
import statistics
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat.embeddings import get_embedder
from app.chat.retrieval import score_topics
from app.db.migrate_db import engine as sync_engine
from app.model.model import Crop

# Topics scored per question; the guard in main() stops the run if the corpus exceeds it.
TOPIC_CAP = 1000

# (question, the topic it is about, the slug of the crop it is about)
QUESTIONS = [
    ("how hot should basil be?", "optimal-temperature", "basil"),
    ("what temperature does basil grow best at?", "optimal-temperature", "basil"),
    ("how often should I water basil?", "watering-needs", "basil"),
    ("what soil pH does basil want?", "soil-ph", "basil"),
    ("what pests attack basil?", "pest-and-disease", "basil"),
    # The only published basil propagation document covers seed-raising and says it is NOT
    # about stem cuttings, so a cuttings question would take its on-topic score from a document
    # that does not answer it (a known gap; keep the question on seed-raising).
    ("how do I raise basil seedlings from seed?", "propagation", "basil"),
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
        for question, topic, crop_slug in QUESTIONS:
            crop_id = session.scalar(select(Crop.id).where(Crop.slug == crop_slug))
            if crop_id is None:
                sys.exit(f"unknown crop {crop_slug!r} in QUESTIONS")
            (vector,) = embedder.embed([question])
            # One row more than the cap: seeing it proves topics were left out. Exactly
            # TOPIC_CAP topics is a complete run, and must not stop.
            scored = score_topics(session, vector, k=TOPIC_CAP + 1, crop_id=crop_id)
            if len(scored) > TOPIC_CAP:
                sys.exit(f"more than {TOPIC_CAP} topics scored for {question!r}: the lowest scores "
                         f"were dropped, so the gap would be unreliable; raise TOPIC_CAP.")
            for _crop_id, name, score in scored:
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
