"""The embedder seam and scripts/reindex.py.

Every test here runs offline: FakeEmbedder is passed in explicitly, or
selected by name, and no test needs OPENAI_API_KEY.
"""
import math
import sys

import openai
import pytest
from sqlalchemy import select, text

from app.chat.embeddings import (
    EMBEDDING_DIMENSIONS,
    FakeEmbedder,
    OpenAIEmbedder,
    embedder_class,
    get_embedder,
)
from app.db.migrate_db import engine as sync_engine
from app.model.model import UNCATEGORISED_SUB_CATEGORY_ID, Crop, Item, ItemChunk
from scripts import reindex as reindex_script
from scripts import seed
from scripts.reindex import reindex

SEED_DOCUMENTS = sum(len(docs) for _, docs in seed._TOPIC_GROUPS)


def _quiet(*_):
    pass


def _run(embedder=None, **kwargs):
    return reindex(sync_engine, embedder or FakeEmbedder(), out=_quiet, **kwargs)


def _chunks_of(session, item_id: int) -> list[ItemChunk]:
    session.expire_all()
    return list(session.execute(
        select(ItemChunk).where(ItemChunk.item_id == item_id).order_by(ItemChunk.chunk_index)
    ).scalars())


def _count(sql: str) -> int:
    with sync_engine.connect() as connection:
        return connection.execute(text(sql)).scalar_one()


# --- the seam ----------------------------------------------------------------

def test_fake_embedder_is_deterministic_unit_norm_and_column_sized():
    a, b = FakeEmbedder().embed(["basil", "basil"])
    (c,) = FakeEmbedder().embed(["tomato"])
    assert a == b
    assert a != c
    assert len(a) == EMBEDDING_DIMENSIONS
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)


def test_embedder_dimensions_match_the_column():
    assert ItemChunk.__table__.c.embedding.type.dim == EMBEDDING_DIMENSIONS


def test_fake_embedder_is_selected_by_env_var_with_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDER", "fake")
    assert isinstance(get_embedder(), FakeEmbedder)


def test_default_is_openai_and_an_unknown_name_is_refused(monkeypatch):
    monkeypatch.delenv("EMBEDDER", raising=False)
    assert embedder_class() is OpenAIEmbedder
    with pytest.raises(ValueError, match="unknown EMBEDDER"):
        embedder_class("opeani")


@pytest.mark.parametrize("key", [None, ""])
def test_openai_embedder_refuses_a_missing_key_before_any_request(monkeypatch, key):
    """docker-compose.yaml always sets OPENAI_API_KEY, so a key-less container
    has it as "": that case must fail at construction, like an unset key."""
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(openai.OpenAIError):
        OpenAIEmbedder()


# --- reindex -----------------------------------------------------------------

def test_reindex_embeds_every_document_then_nothing():
    seed.main()
    first = _run()
    assert (first.documents, first.chunks, first.embedded) == (SEED_DOCUMENTS, SEED_DOCUMENTS, SEED_DOCUMENTS)
    assert _count("SELECT count(*) FROM item_chunks") == SEED_DOCUMENTS

    second = _run()
    assert second.embedded == 0 and second.deleted == 0


def test_drafts_are_embedded_but_the_view_exposes_only_published():
    seed.main()
    _run()
    unpublished = _count("SELECT count(*) FROM items WHERE NOT published")
    assert unpublished >= 1, "the seed corpus is expected to carry a draft"
    assert _count("SELECT count(*) FROM item_chunks") == SEED_DOCUMENTS
    assert _count("SELECT count(*) FROM published_item_chunks") == SEED_DOCUMENTS - unpublished
    assert _count(
        "SELECT count(*) FROM published_item_chunks v JOIN items i ON i.id = v.source_id "
        "WHERE NOT i.published"
    ) == 0
    assert _count(
        f"SELECT count(*) FROM published_item_chunks WHERE content LIKE '%{seed.DRAFT_BODY_MARKER}%'"
    ) == 0


def test_body_edit_re_embeds_that_document_only(sync_db_session):
    seed.main()
    _run()
    item = sync_db_session.execute(select(Item).order_by(Item.id)).scalars().first()
    before = _chunks_of(sync_db_session, item.id)[0].content_hash

    item.body = item.body + " Edited."
    sync_db_session.commit()

    assert _run().embedded == 1
    after = _chunks_of(sync_db_session, item.id)[0]
    assert after.content_hash != before
    assert after.content.endswith(" Edited.")


def test_title_only_edit_also_re_embeds(sync_db_session):
    seed.main()
    _run()
    item = sync_db_session.execute(select(Item).order_by(Item.id)).scalars().first()
    before = _chunks_of(sync_db_session, item.id)[0].content_hash

    item.title = item.title + " (revised)"
    sync_db_session.commit()

    assert _run().embedded == 1
    after = _chunks_of(sync_db_session, item.id)[0]
    assert after.content_hash != before
    assert after.content.startswith(item.title + "\n")


def test_a_new_chunk_is_embedded_when_it_is_created():
    seed.main()
    _run()
    # Both come from now() in the inserting transaction, so they are equal.
    assert _count("SELECT count(*) FROM item_chunks WHERE embedded_at IS DISTINCT FROM created_at") == 0


def test_re_embedding_moves_embedded_at_but_not_created_at(sync_db_session):
    seed.main()
    _run()
    item = sync_db_session.execute(select(Item).order_by(Item.id)).scalars().first()
    # Backdated, so "moved" does not depend on two transactions' now() differing.
    with sync_engine.begin() as connection:
        connection.execute(text(
            "UPDATE item_chunks SET created_at = created_at - interval '1 hour', "
            "embedded_at = embedded_at - interval '1 hour'"
        ))
    before = _chunks_of(sync_db_session, item.id)[0]
    created_before, embedded_before = before.created_at, before.embedded_at

    item.title = item.title + " (revised)"
    sync_db_session.commit()
    assert _run().embedded == 1

    after = _chunks_of(sync_db_session, item.id)[0]
    assert after.created_at == created_before
    assert after.embedded_at > embedded_before


def test_switching_embedder_re_embeds_rather_than_mixing_models():
    class OtherFake(FakeEmbedder):
        model = "other-fake"

    seed.main()
    _run()
    assert _run(OtherFake()).embedded == SEED_DOCUMENTS
    assert _count("SELECT count(DISTINCT model) FROM item_chunks") == 1


def _long_body(paragraphs: int) -> str:
    return "\n\n".join(f"paragraph {n} " + "z" * 600 for n in range(paragraphs))


def test_shrinking_a_document_leaves_no_orphan_chunks(sync_db_session):
    crop = Crop(slug="shrink-probe", common_name="probe", scientific_name="Probe")
    sync_db_session.add(crop)
    sync_db_session.flush()
    item = Item(sub_category_id=UNCATEGORISED_SUB_CATEGORY_ID, crop_id=crop.id, title="Long",
                body=_long_body(3), published=True, source="s", reference="r", url="u",
                read_directly=True)
    sync_db_session.add(item)
    sync_db_session.commit()

    assert _run().chunks == 3
    assert [c.chunk_index for c in _chunks_of(sync_db_session, item.id)] == [0, 1, 2]

    item.body = _long_body(2)  # 3 chunks -> 2: the first two are unchanged
    sync_db_session.commit()
    result = _run()
    assert (result.embedded, result.deleted) == (0, 1)
    assert [c.chunk_index for c in _chunks_of(sync_db_session, item.id)] == [0, 1]

    item.body = "Short now."   # 2 chunks -> 1, and the survivor's text changed
    sync_db_session.commit()
    result = _run()
    assert (result.embedded, result.deleted) == (1, 1)
    remaining = _chunks_of(sync_db_session, item.id)
    assert [c.chunk_index for c in remaining] == [0]
    assert remaining[0].content == "Long\nShort now."


def test_deleting_a_document_cascades_to_its_chunks(sync_db_session):
    seed.main()
    _run()
    item = sync_db_session.execute(select(Item).order_by(Item.id)).scalars().first()
    sync_db_session.delete(item)
    sync_db_session.commit()
    assert _count(f"SELECT count(*) FROM item_chunks WHERE item_id = {item.id}") == 0


def test_dry_run_counts_without_a_key_and_writes_nothing(monkeypatch):
    """No embedder is constructed on a dry run, so the default (openai) path
    needs no key here."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDER", raising=False)
    seed.main()
    lines = []
    result = reindex(sync_engine, dry_run=True, out=lines.append)
    assert result.chunks == SEED_DOCUMENTS and result.embedded == 0
    assert f"{SEED_DOCUMENTS} chunks" in lines[0]
    assert _count("SELECT count(*) FROM item_chunks") == 0


def test_the_cli_entry_point_runs(monkeypatch, capsys):
    """README step 5 runs `python -m scripts.reindex`; the tests above call
    reindex() directly, so this drives main() itself."""
    seed.main()
    monkeypatch.setattr(sys, "argv", ["reindex", "--dry-run"])
    monkeypatch.setattr(sync_engine, "echo", sync_engine.echo)  # main() turns it off
    reindex_script.main()
    out = capsys.readouterr().out
    assert f"{SEED_DOCUMENTS} documents, {SEED_DOCUMENTS} chunks" in out
    assert "dry run: nothing embedded, nothing written" in out
    assert _count("SELECT count(*) FROM item_chunks") == 0
