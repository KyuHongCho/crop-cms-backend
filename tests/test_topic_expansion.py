"""Vector topic selection (app/chat/retrieval.py).

FakeEmbedder vectors are near-orthogonal, so they cannot express "similar".
Every ranking test here inserts hand-built vectors straight into item_chunks:
unit axis `e(i)` is the query direction when i == 0, and `mix(c)` is a unit
vector whose cosine similarity with it is exactly c.
"""
import io
import math
import os
import pathlib
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.chat.embeddings import EMBEDDING_DIMENSIONS, FakeEmbedder
from app.chat.retrieval import (
    NoRelevantTopics,
    retrieve_topics,
    score_topics,
    select_topics,
)
from app.crud.retrieval import TopicCandidate
from app.db.migrate_db import engine as sync_engine
from app.model.model import UNCATEGORISED_SUB_CATEGORY_ID, Crop, Item, ItemChunk
from scripts import seed
from scripts.ask import ask
from scripts.reindex import reindex

ROOT = str(pathlib.Path(__file__).resolve().parent.parent)


def e(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIMENSIONS
    v[i] = 1.0
    return v


def mix(c: float) -> list[float]:
    """Unit vector with cosine similarity c to e(0)."""
    v = [0.0] * EMBEDDING_DIMENSIONS
    v[0] = c
    v[1] = math.sqrt(1.0 - c * c)
    return v


QUERY = e(0)


class FixedEmbedder:
    model = "fixed"

    def embed(self, texts):
        return [QUERY for _ in texts]


@pytest.fixture
def session():
    with Session(sync_engine) as s:
        yield s


def _crop(slug: str = "basil") -> int:
    with sync_engine.begin() as c:
        return c.execute(
            Crop.__table__.insert().values(slug=slug, common_name=slug, scientific_name="x")
        ).inserted_primary_key[0]


def _doc(crop_id: int, topic: str, n: int, vectors: list[list[float]], *, published=True) -> list[int]:
    """One document per vector, one chunk each, all under `topic`."""
    ids = []
    with sync_engine.begin() as c:
        for i, vector in enumerate(vectors):
            item_id = c.execute(Item.__table__.insert().values(
                sub_category_id=UNCATEGORISED_SUB_CATEGORY_ID, crop_id=crop_id, topic=topic,
                title=f"{topic} {n}-{i}", body="body", published=published,
                source=f"Source {topic} {n}-{i}", reference="ref", url="https://example.invalid",
                read_directly=True,
            )).inserted_primary_key[0]
            c.execute(ItemChunk.__table__.insert().values(
                item_id=item_id, chunk_index=0, content="c", content_hash=f"h{item_id}",
                embedding=vector, model="hand",
            ))
            ids.append(item_id)
    return ids


def _scores(session, k=10):
    return {topic: score for _, topic, score in score_topics(session, QUERY, k)}


# --- Rule 1: MAX, not mean ---------------------------------------------------

def test_max_beats_mean_when_one_chunk_is_strong_and_the_rest_weak(session):
    crop = _crop()
    # strong: one 0.9 chunk plus four orthogonal ones -> MAX 0.9, mean 0.18
    _doc(crop, "strong", 0, [mix(0.9)] + [e(5 + i) for i in range(4)])
    # steady: three 0.6 chunks -> MAX 0.6, mean 0.6
    _doc(crop, "steady", 0, [mix(0.6)] * 3)

    ranked = score_topics(session, QUERY, 10)
    assert [t for _, t, _ in ranked] == ["strong", "steady"]
    assert ranked[0][2] == pytest.approx(0.9)
    assert ranked[1][2] == pytest.approx(0.6)
    # the point of the case: a mean would have ranked them the other way round
    assert (0.9 + 4 * 0.0) / 5 < 0.6


def test_unpublished_chunks_do_not_score(session):
    crop = _crop()
    _doc(crop, "live", 0, [mix(0.3)])
    _doc(crop, "draft-only", 0, [mix(0.99)], published=False)
    assert list(_scores(session)) == ["live"]


# --- Rule 2: k ---------------------------------------------------------------

def _five_topics():
    crop = _crop()
    for i, c in enumerate([0.9, 0.8, 0.7, 0.6, 0.5]):
        _doc(crop, f"t{i}", 0, [mix(c)])


def test_k_defaults_to_3_and_limits_topics(session):
    import app.chat.retrieval as r
    assert r.TOPIC_SELECTION_K == 3
    _five_topics()
    assert [t for _, t, _ in score_topics(session, QUERY)] == ["t0", "t1", "t2"]
    assert len(score_topics(session, QUERY, k=2)) == 2


def test_k_env_override_reaches_the_chat_layer():
    # Subprocess: the value is read at import time (see tests/test_retrieval.py).
    result = subprocess.run(
        [sys.executable, "-c", "import app.chat.retrieval as r; print(r.TOPIC_SELECTION_K)"],
        env={**os.environ, "TOPIC_SELECTION_K": "5"}, capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "5"


def test_floor_env_override_and_dark_default():
    def run(env):
        return subprocess.run(
            [sys.executable, "-c", "import app.chat.retrieval as r; print(r.TOPIC_SCORE_FLOOR)"],
            env={**{k: v for k, v in os.environ.items() if k != "TOPIC_SCORE_FLOOR"}, **env},
            capture_output=True, text=True, cwd=ROOT,
        ).stdout.strip()
    assert run({}) == "-1.0"
    assert run({"TOPIC_SCORE_FLOOR": "0.4"}) == "0.4"


# --- Rule 0: the floor -------------------------------------------------------

def _cand(topic, score):
    return TopicCandidate(topic=topic, score=score, documents=[])


def test_default_floor_is_behaviour_preserving_even_for_negative_cosine():
    cands = [_cand("a", 0.5), _cand("b", -0.2), _cand("c", 0.1), _cand("d", 0.3)]
    default = select_topics(cands)
    assert [c.topic for c in default] == ["a", "d", "c"]                 # Rule 2, k=3
    assert default == select_topics(cands, floor=-1.0)
    # 0.0 is NOT a no-op on raw cosine: it would abstain on an all-negative result.
    with pytest.raises(NoRelevantTopics):
        select_topics([_cand("b", -0.2)], floor=0.0)
    assert select_topics([_cand("b", -0.2)])[0].topic == "b"


def test_a_floor_above_every_score_abstains():
    with pytest.raises(NoRelevantTopics):
        select_topics([_cand("a", 0.5), _cand("b", 0.4)], floor=0.51)
    with pytest.raises(NoRelevantTopics):
        select_topics([])


def test_floor_drops_whole_topics_never_documents_inside_one(session):
    crop = _crop()
    # one topic whose three documents straddle the floor (0.50 / 0.30 / 0.10)
    _doc(crop, "split", 0, [mix(0.50), mix(0.30), mix(0.10)])
    _doc(crop, "weak", 0, [mix(0.20)])
    kept, _ = retrieve_topics(session, "q", FixedEmbedder(), floor=0.25)
    assert [c.topic for c in kept] == ["split"]
    assert kept[0].document_count == 3        # the 0.10 and 0.30 documents survive


# --- complete topics ---------------------------------------------------------

def test_topics_come_back_complete_and_limit_applies_to_topics_only(session):
    crop = _crop()
    for i, c in enumerate([0.9, 0.8, 0.7, 0.6]):
        _doc(crop, f"t{i}", 0, [mix(c), e(7), e(8), e(9), e(10)])      # 5 documents each
    kept, dropped = retrieve_topics(session, "q", FixedEmbedder())
    assert [c.topic for c in kept] == ["t0", "t1", "t2"] and dropped == []
    assert [c.document_count for c in kept] == [5, 5, 5]


def test_a_topic_name_shared_by_two_crops_is_scored_per_crop(session):
    a, b = _crop("a"), _crop("b")
    _doc(a, "shared", 0, [mix(0.9)])
    _doc(b, "shared", 0, [mix(0.4)])
    kept, _ = retrieve_topics(session, "q", FixedEmbedder())
    assert [(c.score, c.document_count) for c in kept] == [
        (pytest.approx(0.9), 1), (pytest.approx(0.4), 1)]


def test_crop_id_limits_scoring_to_one_crop(session):
    basil, crop_b = _crop("basil"), _crop("crop-b")
    _doc(basil, "optimal-temperature", 0, [mix(0.30)])
    _doc(crop_b, "optimal-temperature", 0, [mix(0.90)])
    both, _ = retrieve_topics(session, "q", FixedEmbedder())
    only_basil, _ = retrieve_topics(session, "q", FixedEmbedder(), crop_id=basil)
    assert [{d.crop_id for d in c.documents} for c in both] == [{crop_b}, {basil}]
    assert [(c.score, {d.crop_id for d in c.documents}) for c in only_basil] == [
        (pytest.approx(0.30), {basil})]


def test_a_crop_with_no_chunks_abstains(session):
    basil, crop_b = _crop("basil"), _crop("crop-b")
    _doc(basil, "optimal-temperature", 0, [mix(0.30)])
    with pytest.raises(NoRelevantTopics):
        retrieve_topics(session, "q", FixedEmbedder(), crop_id=crop_b)


def test_ask_prints_only_the_requested_crop(session):
    basil, crop_b = _crop("basil"), _crop("crop-b")
    _doc(basil, "optimal-temperature", 0, [mix(0.30)])
    _doc(crop_b, "optimal-temperature", 0, [mix(0.90)])
    lines: list[str] = []
    assert ask("q", embedder=FixedEmbedder(), session=session, out=lines.append, crop_id=basil) == 0
    headers = [l.strip() for l in lines if l.strip().startswith("==")]
    assert headers == ["== optimal-temperature  (score 0.3000, 1 documents)"]


# --- Done 1: the basil corpus ------------------------------------------------

def _seeded_corpus_with_temperature_near_the_query():
    seed.main()
    reindex(sync_engine, FakeEmbedder(), out=lambda *_: None)
    with sync_engine.begin() as c:
        # Put the three temperature chunks near the query at three distinct similarities
        # (illustrative values, not a measurement), so a per-document floor between them
        # would keep some documents and drop others.
        ids = [r[0] for r in c.execute(text(
            "SELECT c.id FROM item_chunks c JOIN items i ON i.id = c.item_id "
            "WHERE i.topic = 'optimal-temperature' ORDER BY c.id"))]
        assert len(ids) == 3
        for chunk_id, score in zip(ids, (0.0976, 0.0729, 0.0538)):
            c.execute(ItemChunk.__table__.update().where(ItemChunk.id == chunk_id)
                      .values(embedding=mix(score)))


def test_basil_temperature_question_returns_all_three_sources(session):
    _seeded_corpus_with_temperature_near_the_query()
    out = io.StringIO()
    code = ask("how hot should basil be?", embedder=FixedEmbedder(), session=session,
               out=lambda s: print(s, file=out))
    text_out = out.getvalue()
    assert code == 0
    for source in ("FAO ECOCROP (id 1547)", "Chang, Alderson & Wright (2005)", "Walters & Currey (2019)"):
        assert f"source: {source}" in text_out
    assert "== optimal-temperature" in text_out and "3 documents" in text_out


def test_ask_runs_end_to_end_offline_with_the_fake_embedder(session):
    # Plumbing only: fake vectors carry no meaning, so which topics win is arbitrary.
    seed.main()
    reindex(sync_engine, FakeEmbedder(), out=lambda *_: None)
    lines: list[str] = []
    assert ask("how hot should basil be?", embedder=FakeEmbedder(), session=session,
               out=lines.append) == 0
    assert 1 <= sum(1 for line in lines if line.startswith("\n==")) <= 3


def test_ask_abstains_below_the_floor(session):
    _seeded_corpus_with_temperature_near_the_query()
    lines: list[str] = []
    assert ask("anything", embedder=FixedEmbedder(), session=session, out=lines.append, floor=0.99) == 1
    assert lines[0].startswith("abstain:")
