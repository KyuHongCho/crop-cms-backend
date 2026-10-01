"""Embeds every document into item_chunks.

    docker compose exec -T cms python -m scripts.reindex             # embed what changed
    docker compose exec -T cms python -m scripts.reindex --dry-run   # count only, embeds nothing

`EMBEDDER` selects the provider (app/chat/embeddings.py): `openai` by default,
which needs OPENAI_API_KEY; `fake` for offline runs.

- **Every** document is embedded, published or not. Publishing is a metadata
  flip; the `published_item_chunks` view filters at read time. Embedding only
  published documents would reproduce, one layer down, the trap
  app/router/item.py documents for GET /items.
- A chunk is skipped when its stored `content_hash` and `model` both match.
  The hash covers the embedded text (title + body), so a title-only edit
  re-embeds too; the model check means switching embedder re-embeds rather
  than leaving a mixed-model table.
- When a document shrinks from N chunks to M, `chunk_index >= M` is deleted in
  the same transaction as the upsert, so no orphan chunk outlives the text it
  came from.
- `--dry-run` reads and counts but never constructs the embedder, so it costs
  nothing and needs no key.

A script rather than app code: it writes the raw table through the ORM
metadata, which app/chat/ is not allowed to name
(tests/test_chat_layer_isolation.py).
"""
import argparse
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from app.chat.chunking import chunk_document, content_hash
from app.chat.embeddings import Embedder, embedder_class, get_embedder
from app.db.migrate_db import engine as sync_engine
from app.model.model import Item, ItemChunk

_items = Item.__table__
_chunks = ItemChunk.__table__


@dataclass
class _DocumentPlan:
    item_id: int
    chunk_count: int
    # (chunk_index, text, hash) for every chunk whose stored copy is missing
    # or out of date.
    to_embed: list[tuple[int, str, str]] = field(default_factory=list)
    stale: int = 0  # stored chunks at index >= chunk_count


@dataclass
class ReindexResult:
    documents: int
    chunks: int
    embedded: int
    deleted: int


def _plan(engine: Engine, model: str) -> list[_DocumentPlan]:
    with engine.connect() as connection:
        documents = connection.execute(
            select(_items.c.id, _items.c.title, _items.c.body).order_by(_items.c.id)
        ).all()
        stored: dict[int, dict[int, tuple[str, str]]] = {}
        for row in connection.execute(
            select(_chunks.c.item_id, _chunks.c.chunk_index, _chunks.c.content_hash, _chunks.c.model)
        ):
            stored.setdefault(row.item_id, {})[row.chunk_index] = (row.content_hash, row.model)

    plans = []
    for document in documents:
        texts = chunk_document(document.title, document.body)
        existing = stored.get(document.id, {})
        plan = _DocumentPlan(item_id=document.id, chunk_count=len(texts))
        for index, text in enumerate(texts):
            digest = content_hash(text)
            if existing.get(index) != (digest, model):
                plan.to_embed.append((index, text, digest))
        plan.stale = sum(1 for index in existing if index >= len(texts))
        plans.append(plan)
    return plans


def _write(engine: Engine, plan: _DocumentPlan, vectors: list[list[float]], model: str) -> None:
    """Upsert the changed chunks and delete the stale ones -- one transaction."""
    with engine.begin() as connection:
        if plan.to_embed:
            statement = insert(_chunks).values([
                dict(item_id=plan.item_id, chunk_index=index, content=text,
                     content_hash=digest, embedding=vector, model=model)
                for (index, text, digest), vector in zip(plan.to_embed, vectors)
            ])
            connection.execute(statement.on_conflict_do_update(
                index_elements=[_chunks.c.item_id, _chunks.c.chunk_index],
                set_=dict(
                    content=statement.excluded.content,
                    content_hash=statement.excluded.content_hash,
                    embedding=statement.excluded.embedding,
                    model=statement.excluded.model,
                ),
            ))
        connection.execute(
            delete(_chunks).where(
                _chunks.c.item_id == plan.item_id,
                _chunks.c.chunk_index >= plan.chunk_count,
            )
        )


def reindex(
    engine: Engine = sync_engine,
    embedder: Embedder | None = None,
    *,
    dry_run: bool = False,
    out=print,
) -> ReindexResult:
    model = embedder.model if embedder is not None else embedder_class().model
    plans = _plan(engine, model)
    to_embed = sum(len(p.to_embed) for p in plans)
    stale = sum(p.stale for p in plans)
    total = sum(p.chunk_count for p in plans)
    out(f"{len(plans)} documents, {total} chunks; {to_embed} to embed with {model}, "
        f"{stale} stale to delete")
    if dry_run:
        out("dry run: nothing embedded, nothing written")
        return ReindexResult(documents=len(plans), chunks=total, embedded=0, deleted=0)

    if embedder is None and to_embed:
        embedder = get_embedder()
    for plan in plans:
        if not plan.to_embed and not plan.stale:
            continue
        vectors = embedder.embed([text for _, text, _ in plan.to_embed]) if plan.to_embed else []
        _write(engine, plan, vectors, model)
    out(f"embedded {to_embed} chunks, deleted {stale} stale chunks")
    return ReindexResult(documents=len(plans), chunks=total, embedded=to_embed, deleted=stale)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the chunk count and what would change, then stop")
    args = parser.parse_args()
    # migrate_db.py's engine is built with echo=True; the SQL log would bury
    # the counts this script exists to print.
    sync_engine.echo = False
    reindex(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
