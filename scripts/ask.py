"""Asks the corpus a question and prints the topics it selects, in full.

    docker compose exec -T cms python -m scripts.ask "how hot should basil be?"

`EMBEDDER` selects the provider (app/chat/embeddings.py): `openai` by default,
which needs OPENAI_API_KEY. With `EMBEDDER=fake` it runs offline, but the fake
vectors carry no meaning, so the topics chosen are arbitrary -- use it to
check the plumbing, not the ranking.

Prints every document of each selected topic with its provenance, and names
anything dropped for the context budget or the abstention (Rule 0).
"""
import argparse
import sys

from sqlalchemy.orm import Session

from app.chat.embeddings import get_embedder
from app.chat.retrieval import NoRelevantTopics, retrieve_topics
from app.crud.retrieval import TopicBudgetExceeded
from app.db.migrate_db import engine as sync_engine


def ask(question: str, embedder=None, session: Session | None = None, out=print) -> int:
    embedder = embedder or get_embedder()
    own = session is None
    session = session or Session(sync_engine)
    try:
        kept, dropped = retrieve_topics(session, question, embedder)
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
    args = parser.parse_args()
    sync_engine.echo = False  # the SQL log would bury the answer
    sys.exit(ask(args.question))


if __name__ == "__main__":
    main()
