"""Asks the corpus a question and prints the topics it selects, in full.

    docker compose exec -T cms python -m scripts.ask "how hot should basil be?"
    docker compose exec -T cms python -m scripts.ask --crop basil "how hot should it be?"

`--crop SLUG` limits scoring to that crop; without it every crop competes, so with more than
one crop a question can select another crop's topic.

`EMBEDDER` selects the provider (app/chat/embeddings.py): `openai` by default,
which needs OPENAI_API_KEY. With `EMBEDDER=fake` it runs offline, but the fake
vectors carry no meaning, so the topics chosen are arbitrary -- use it to
check the plumbing, not the ranking.

Prints every document of each selected topic with its provenance, and names
anything dropped for the context budget or the abstention (Rule 0).
"""
import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat.embeddings import get_embedder
from app.chat.retrieval import NoRelevantTopics, retrieve_topics
from app.crud.retrieval import TopicBudgetExceeded
from app.db.migrate_db import engine as sync_engine
from app.model.model import Crop


def ask(question: str, embedder=None, session: Session | None = None, out=print,
        floor: float | None = None, crop_id: int | None = None) -> int:
    embedder = embedder or get_embedder()
    own = session is None
    session = session or Session(sync_engine)
    try:
        options = {} if floor is None else {"floor": floor}
        kept, dropped = retrieve_topics(session, question, embedder, crop_id=crop_id, **options)
    except NoRelevantTopics as exc:
        out(f"abstain: {exc}")
        return 1
    except TopicBudgetExceeded as exc:
        out(f"refused: {exc}")
        return 2
    finally:
        if own:
            session.close()

    for candidate in kept:
        out(f"\n== {candidate.topic}  (score {candidate.score:.4f}, "
            f"{candidate.document_count} documents)")
        for document in candidate.documents:
            out(f"  - {document.title}")
            out(f"      source: {document.source}")
            out(f"      reference: {document.reference}")
            out(f"      url: {document.url}")
            out(f"      {document.body}")
    for candidate in dropped:
        out(f"\ndropped for the context budget: {candidate.topic} (score {candidate.score:.4f})")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--crop", metavar="SLUG", help="limit to one crop (default: every crop)")
    args = parser.parse_args()
    sync_engine.echo = False  # the SQL log would bury the answer
    crop_id = None
    if args.crop:
        with Session(sync_engine) as session:
            crop_id = session.scalar(select(Crop.id).where(Crop.slug == args.crop))
        if crop_id is None:
            parser.error(f"unknown crop {args.crop!r}")
    sys.exit(ask(args.question, crop_id=crop_id))


if __name__ == "__main__":
    main()
