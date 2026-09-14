"""Topic-set retrieval: the no-truncation guarantee.

A common RAG pattern asks for the top-k most similar documents
(`similarity_search(query, k=N)`). This system does not: `k` will select
*topics*, and every selected topic returns *complete*.

Why: published sources disagree. Basil's optimal temperature is carried as
three attributed claims that contradict each other (FAO ECOCROP,
Chang/Alderson/Wright, Walters & Currey). A `LIMIT 1` or `LIMIT 2` over that set
silently drops at least one of them, with nothing in the response to say so.

That is why several tests below check that something is *absent* -- no LIMIT
in the SQL, no `limit` parameter in the API -- rather than that a feature is
present.
"""
import logging
import os
import pathlib
import subprocess
import sys

import pytest
from sqlalchemy.dialects import postgresql

import app.crud.retrieval as retrieval
from app.db.migrate_db import engine as sync_engine
from app.model.model import UNCATEGORISED_SUB_CATEGORY_ID, Crop, Item
from app.schema.retrieval import RetrievedDocument


def _make_crop(slug: str) -> int:
    with sync_engine.begin() as connection:
        result = connection.execute(
            Crop.__table__.insert().values(
                slug=slug, common_name=slug, scientific_name="Ocimum basilicum"
            )
        )
        return result.inserted_primary_key[0]


def _make_items(
    crop_id: int,
    topic: str,
    count: int,
    *,
    published: bool = True,
    body: str = "a body",
    title_prefix: str = "doc",
) -> None:
    rows = [
        dict(
            sub_category_id=UNCATEGORISED_SUB_CATEGORY_ID,
            crop_id=crop_id,
            topic=topic,
            title=f"{title_prefix} {index}",
            body=body,
            published=published,
            source=f"Source {index}",
            reference=f"Reference {index}",
            url=f"https://example.invalid/{index}",
            read_directly=True,
        )
        for index in range(count)
    ]
    with sync_engine.begin() as connection:
        connection.execute(Item.__table__.insert(), rows)


def _document(topic: str = "t", body: str = "b", index: int = 0) -> RetrievedDocument:
    """A RetrievedDocument built in memory, for the pure budget-policy tests."""
    return RetrievedDocument(
        id=index,
        topic=topic,
        title=f"doc {index}",
        body=body,
        source=f"Source {index}",
        reference=f"Reference {index}",
        url=f"https://example.invalid/{index}",
        read_directly=True,
        via=None,
        condition=None,
        licence_note=None,
    )


# --- the guarantee itself ----------------------------------------------------


def test_twelve_documents_under_one_topic_all_come_back_with_provenance(client):
    """Twelve is more than any plausible `k` (3 by default). A top-k slice would
    return 3; this endpoint must return all twelve."""
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 12)

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["document_count"] == 12
    assert len(payload["documents"]) == 12
    for document in payload["documents"]:
        assert document["source"]
        assert document["reference"]
        assert document["url"]


def test_a_normal_response_always_carries_a_dropped_field(client):
    """`dropped` is always present. With one topic per request today it is
    always empty, but multi-topic selection can fill it later without changing
    the response shape (TopicSetResponse in app/schema/retrieval.py)."""
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 2)

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    assert response.json()["dropped"] == []


def test_unpublished_documents_are_excluded(client):
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 12)
    _make_items(
        crop_id, "optimal-temperature", 3, published=False, title_prefix="draft"
    )

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    titles = [document["title"] for document in response.json()["documents"]]
    assert len(titles) == 12
    assert not [title for title in titles if title.startswith("draft")], titles


def test_documents_of_another_crop_sharing_the_topic_are_excluded(client):
    basil_id = _make_crop("basil")
    coriander_id = _make_crop("coriander")
    _make_items(basil_id, "optimal-temperature", 12)
    _make_items(coriander_id, "optimal-temperature", 4, title_prefix="coriander")

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    titles = [document["title"] for document in response.json()["documents"]]
    assert len(titles) == 12
    assert not [title for title in titles if title.startswith("coriander")], titles


def test_documents_under_another_topic_are_excluded(client):
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 12)
    _make_items(crop_id, "watering-needs", 5, title_prefix="watering")

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    titles = [document["title"] for document in response.json()["documents"]]
    assert len(titles) == 12
    assert not [title for title in titles if title.startswith("watering")], titles


# --- the absence assertions --------------------------------------------------


def test_the_topic_set_statement_compiles_without_a_limit():
    """Asserted against the compiled statement, not just the executed one, so
    it fails at the point a LIMIT is written rather than only when a request
    happens to run."""
    sql = str(
        retrieval.topic_set_statement(crop_id=1, topic="optimal-temperature").compile(
            dialect=postgresql.dialect()
        )
    )
    assert "LIMIT" not in sql.upper(), sql
    assert "FETCH" not in sql.upper(), sql


def test_the_sql_actually_executed_contains_no_limit(client, caplog):
    """The other half of the pair above: `echo=True` (app/db/db.py:16) logs
    every statement, so what PostgreSQL was really asked is observable."""
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 12)

    with caplog.at_level(logging.INFO, logger="sqlalchemy.engine.Engine"):
        response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    logged = [
        record.getMessage()
        for record in caplog.records
        if "FROM items" in record.getMessage()
    ]
    assert logged, (
        "no SELECT against items was logged -- echo=True (db.py:16) should have "
        "logged it; this test cannot see a LIMIT it never captured"
    )
    for statement in logged:
        assert "LIMIT" not in statement.upper(), statement


def test_the_api_surface_carries_no_limit_parameter(client):
    """No `limit` parameter: a caller cannot ask for a truncated topic set,
    even deliberately."""
    schema = client.get("/openapi.json").json()
    path = schema["paths"]["/retrieval/{crop_slug}/{topic}"]["get"]
    names = [parameter["name"] for parameter in path.get("parameters", [])]
    assert names == ["crop_slug", "topic"], names


# --- edges -------------------------------------------------------------------


def test_an_unknown_crop_is_404(client):
    _make_crop("basil")

    response = client.get("/retrieval/nosuchcrop/optimal-temperature")

    assert response.status_code == 404, response.text
    assert "nosuchcrop" in response.json()["detail"]


def test_a_topic_with_no_published_documents_returns_an_empty_set_not_404(client):
    """200 with no documents, not 404: `topic` is free text with no registry
    to check it against, and here the topic does exist -- its documents are
    all unpublished drafts."""
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 3, published=False)

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    assert response.json()["document_count"] == 0
    assert response.json()["documents"] == []


# --- Topic selection and the context budget ----------------------------------
#
# Three rules: (1) score a topic by its best-matching chunk, (2) keep the top k
# topics, (3) fit the kept topics into the context budget. Rules 1 and 2 need
# embeddings, which do not exist yet, so only k's value is checked. Rule 3 is
# built and tested below with hand-made TopicCandidates, ready for real scores.


def test_k_defaults_to_3():
    """Rule 2: k is 3 by default. There is no topic selection to run it
    through yet, so this checks the constant directly."""
    assert retrieval.TOPIC_SELECTION_K == 3


def test_k_is_configurable_via_env_var():
    """Runs in a subprocess because the value is read at import time.

    Reloading the module in this process (importlib.reload) would create a new
    TopicBudgetExceeded class that app/router/retrieval.py's `except` no
    longer matches, breaking later tests with an unrelated 500.
    """
    env = {**os.environ, "TOPIC_SELECTION_K": "5"}
    result = subprocess.run(
        [sys.executable, "-c", "import app.crud.retrieval as r; print(r.TOPIC_SELECTION_K)"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "5"


def test_context_token_budget_is_configurable_via_env_var():
    """Subprocess for the same reason as the test above. Also checks the
    derived CONTEXT_CHAR_BUDGET (tokens x CHARS_PER_TOKEN), so a misspelled env
    var that silently fell back to the default would fail here."""
    env = {**os.environ, "CONTEXT_TOKEN_BUDGET": "100"}
    result = subprocess.run(
        [sys.executable, "-c",
         "import app.crud.retrieval as r; print(r.CONTEXT_TOKEN_BUDGET, r.CONTEXT_CHAR_BUDGET)"],
        env=env, capture_output=True, text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "100 400"


def test_assemble_within_budget_keeps_everything_when_it_all_fits():
    candidates = [
        retrieval.TopicCandidate(topic="a", score=0.9, documents=[_document(body="x" * 100)]),
        retrieval.TopicCandidate(topic="b", score=0.5, documents=[_document(body="y" * 100)]),
    ]

    kept, dropped = retrieval.assemble_within_budget(candidates, budget=10_000)

    assert [c.topic for c in kept] == ["a", "b"]
    assert dropped == []


def test_assemble_within_budget_keeps_everything_when_combination_exactly_equals_budget():
    """Boundary: a combination exactly AT the budget fits, so nothing is
    dropped. Catches a `>` turned into `>=`; no other combination test sits
    exactly on the budget."""
    a = retrieval.TopicCandidate(topic="a", score=0.9, documents=[_document(index=0, body="x" * 95)])
    b = retrieval.TopicCandidate(topic="b", score=0.5, documents=[_document(index=0, body="y" * 95)])
    # Each candidate is len("doc 0") + 95 == 100 characters; the budget below
    # is set to their combined total (200) exactly.
    assert a.context_chars == 100
    assert b.context_chars == 100

    kept, dropped = retrieval.assemble_within_budget([a, b], budget=200)

    assert [c.topic for c in kept] == ["a", "b"]
    assert dropped == []


def test_assemble_within_budget_does_not_refuse_a_single_topic_exactly_at_budget():
    """Boundary: a single topic exactly AT the budget is kept, not refused.
    Catches a `>` turned into `>=`; the other refusal tests are well over."""
    candidate = retrieval.TopicCandidate(
        topic="exact", score=1.0, documents=[_document(index=0, body="z" * 95)],
    )
    # len("doc 0") + 95 == 100 -- the budget below is set to that exact figure.
    assert candidate.context_chars == 100

    kept, dropped = retrieval.assemble_within_budget([candidate], budget=100)

    assert kept == [candidate]
    assert dropped == []


def test_assemble_within_budget_drops_whole_topics_lowest_score_first():
    """Over budget: drop whole topics, lowest score first, until the rest
    fits -- and name what was dropped. Each topic here fits alone; all three
    together do not."""
    high = retrieval.TopicCandidate(topic="high", score=0.9, documents=[_document(body="h" * 40)])
    mid = retrieval.TopicCandidate(topic="mid", score=0.5, documents=[_document(body="m" * 40)])
    low = retrieval.TopicCandidate(topic="low", score=0.1, documents=[_document(body="l" * 40)])
    # Each is 45 chars (40 + len("doc 0")); the budget is 50. All three = 135,
    # without "low" = 90, without "mid" too = 45, which fits.

    kept, dropped = retrieval.assemble_within_budget([mid, low, high], budget=50)

    assert [c.topic for c in kept] == ["high"]
    assert [c.topic for c in dropped] == ["low", "mid"], (
        "dropped topics must be named, lowest-score-first"
    )
    # Never partially truncated: the survivor's document set is untouched.
    assert kept[0].documents == high.documents


def test_assemble_within_budget_never_partially_truncates_a_kept_topic():
    """A kept topic's document set is bit-for-bit what was passed in -- Rule 3
    forbids dropping some of a topic's documents while keeping the rest."""
    documents = [_document(index=i, body="z" * 10) for i in range(5)]
    candidate = retrieval.TopicCandidate(topic="t", score=1.0, documents=documents)

    kept, dropped = retrieval.assemble_within_budget([candidate], budget=10_000)

    assert dropped == []
    assert len(kept[0].documents) == 5
    assert kept[0].documents == documents


def test_assemble_within_budget_refuses_when_a_single_topic_alone_exceeds_budget():
    """Rule 3 clause 2: refuse only when a single topic ALONE exceeds the
    budget -- dropping every other topic could not make it fit."""
    oversized = retrieval.TopicCandidate(
        topic="huge", score=1.0, documents=[_document(index=i, body="x" * 1000) for i in range(5)],
    )
    small = retrieval.TopicCandidate(topic="small", score=0.1, documents=[_document(body="y")])

    with pytest.raises(retrieval.TopicBudgetExceeded) as excinfo:
        retrieval.assemble_within_budget([oversized, small], budget=500)

    assert excinfo.value.topic == "huge"
    assert excinfo.value.document_count == 5


def test_assemble_within_budget_drops_an_oversized_low_scoring_topic_to_save_two_smaller_ones():
    """Regression: an oversized topic that also scores lowest is dropped,
    letting the two smaller topics through. Earlier code checked for oversized
    topics before dropping anything, and refused the whole request."""
    good_a = retrieval.TopicCandidate(topic="good-a", score=0.9, documents=[_document(body="a" * 20)])
    good_b = retrieval.TopicCandidate(topic="good-b", score=0.8, documents=[_document(body="b" * 20)])
    huge = retrieval.TopicCandidate(topic="huge", score=0.1, documents=[_document(body="h" * 1000)])
    # good_a and good_b: 25 chars each (5 + 20). huge: 1005 (5 + 1000). Budget: 100.
    assert good_a.context_chars == 25
    assert good_b.context_chars == 25
    assert huge.context_chars == 1005

    kept, dropped = retrieval.assemble_within_budget([good_a, good_b, huge], budget=100)

    assert [c.topic for c in kept] == ["good-a", "good-b"]
    assert [c.topic for c in dropped] == ["huge"]


def test_assemble_within_budget_drops_the_lower_scored_oversized_topic_then_refuses_the_survivor():
    """Both topics are too big on their own: drop the lower-scored one first,
    then refuse, naming the survivor -- never the dropped topic, and never an
    empty result."""
    high = retrieval.TopicCandidate(
        topic="high", score=0.9, documents=[_document(index=i, body="x" * 30) for i in range(5)],
    )
    low = retrieval.TopicCandidate(
        topic="low", score=0.5, documents=[_document(index=i, body="y" * 40) for i in range(5)],
    )
    # high: 5 x (5 + 30) = 175 chars; low: 5 x (5 + 40) = 225. Budget: 100.
    assert high.context_chars == 175
    assert low.context_chars == 225

    with pytest.raises(retrieval.TopicBudgetExceeded) as excinfo:
        retrieval.assemble_within_budget([high, low], budget=100)

    assert excinfo.value.topic == "high"
    assert excinfo.value.document_count == 5


def test_an_oversized_topic_is_refused_with_413_naming_it_and_its_count(client):
    """Over HTTP: a topic too large for the default budget (32,000 chars) gets
    a 413 naming the topic and its document count. Twelve 3,000-char documents
    exceed the default, so no config override is needed."""
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 12, body="x" * 3000)

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 413, response.text
    detail = response.json()["detail"]
    assert detail["topic"] == "optimal-temperature"
    assert detail["document_count"] == 12
    assert detail["reason"] == "topic_alone_exceeds_context_budget"


# --- topic casing/whitespace normalization ------------------------------------
#
# Item.topic is normalized by a SQLAlchemy @validates hook on the model
# (Item._normalize_topic), not a Pydantic validator: scripts/seed.py builds Item
# objects directly and never goes through Pydantic. The hook covers both the
# API and the seed script.


def test_casing_variant_topics_are_unified_over_the_real_http_endpoint(client):
    """Two documents posted via the real POST /items endpoint with a
    casing/whitespace-variant topic must come back together as one topic
    set, not two silently disjoint ones."""
    crop_id = _make_crop("basil")

    def _post(topic: str, title: str) -> None:
        response = client.post(
            "/items",
            json=dict(
                sub_category_id=UNCATEGORISED_SUB_CATEGORY_ID,
                crop_id=crop_id,
                topic=topic,
                title=title,
                body="a body",
                published=True,
                source="s",
                reference="r",
                url="u",
                read_directly=True,
            ),
        )
        assert response.status_code == 201, response.text

    _post("optimal-temperature", "lowercase")
    _post(" Optimal-Temperature ", "cased and padded")

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    assert response.json()["document_count"] == 2


def test_casing_variant_topics_are_unified_via_the_seed_script_write_pattern(sync_db_session):
    """The seed script's write path: Item objects built directly with the ORM,
    bypassing HTTP and Pydantic. It must normalize topics too."""
    crop_id = _make_crop("basil")

    def _write(topic: str, title: str) -> None:
        item = Item(
            sub_category_id=UNCATEGORISED_SUB_CATEGORY_ID,
            crop_id=crop_id,
            topic=topic,
            title=title,
            body="a body",
            published=True,
            source="s",
            reference="r",
            url="u",
            read_directly=True,
        )
        sync_db_session.add(item)

    _write("optimal-temperature", "lowercase")
    _write(" Optimal-Temperature ", "cased and padded")
    sync_db_session.commit()

    result = sync_db_session.query(Item).filter_by(crop_id=crop_id).all()
    assert {item.topic for item in result} == {"optimal-temperature"}


def test_normalization_does_not_retroactively_heal_a_pre_fix_row(client):
    """Known limitation: rows written without the ORM are not normalized after
    the fact. A Core insert (used here) skips @validates, so this row keeps its
    original casing. Existing data would need a one-time backfill; none is
    included because the dev database holds no un-normalized topics."""
    crop_id = _make_crop("basil")
    with sync_engine.begin() as connection:
        connection.execute(
            Item.__table__.insert(),
            [
                dict(
                    sub_category_id=UNCATEGORISED_SUB_CATEGORY_ID,
                    crop_id=crop_id,
                    topic=" Optimal-Temperature ",  # never normalized -- Core insert
                    title="legacy row",
                    body="a body",
                    published=True,
                    source="s",
                    reference="r",
                    url="u",
                    read_directly=True,
                )
            ],
        )

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    assert response.json()["document_count"] == 0
