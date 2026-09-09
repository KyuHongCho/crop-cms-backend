"""Topic-set retrieval: the no-truncation guarantee.

**The deliberate departure from the course.** The course this repository is
built alongside teaches RAG as `similarity_search(query, k=N)` -- a top-k slice
of *documents*. This system does not do that. `k` selects **topics**; every
selected topic then returns **complete**.

The reason is `app/model/model.py:92-94`, verbatim:

    "retrieval returns every document sharing a `topic` rather than a top-k
     slice -- otherwise a LIMIT silently picks a winner among disagreeing
     sources."

Basil's optimal temperature is carried as three attributed claims that
contradict each other (FAO ECOCROP, Chang/Alderson/Wright, Walters & Currey).
A `LIMIT 1` -- or a `LIMIT 2` -- over that set does not return "the best
answer"; it returns *one side of an open disagreement*, silently, with nothing
in the response to say a rival source was dropped. That is the failure this
file exists to make impossible, so several tests below assert the *absence* of
things (no LIMIT in the SQL, no `limit` parameter in the OpenAPI surface)
rather than the presence of a feature.
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
    """Twelve is deliberately larger than any plausible `k` (k=3 today).

    If retrieval were a top-k document slice -- what the course teaches -- this
    would return 3. model.py:92-94 requires all twelve.
    """
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
    """`dropped` is on every successful response, not added only once real
    multi-topic selection exists to populate it -- see
    app/schema/retrieval.py's TopicSetResponse and app/router/retrieval.py.
    Today there is only ever one candidate, so it is always empty, but the
    field itself is present now so that later work is not a response-shape
    change.
    """
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
    """No `limit` parameter, per this endpoint's design goal. A caller must
    not be able to ask for a truncated topic set at all -- not even by
    opting in."""
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
    """200 with zero documents, deliberately -- not 404.

    `topic` is a free-text column with no registry table behind it, so there is
    no such thing as "an unknown topic" to 404 on; and a 404 would be an
    outright lie for a topic that exists but whose documents are all drafts,
    which is exactly the case constructed here.
    """
    crop_id = _make_crop("basil")
    _make_items(crop_id, "optimal-temperature", 3, published=False)

    response = client.get("/retrieval/basil/optimal-temperature")

    assert response.status_code == 200, response.text
    assert response.json()["document_count"] == 0
    assert response.json()["documents"] == []


# --- Rules 1-3 -- N3: decided here, only rule 3 built and tested here --------
#
# Rules 1 (MAX chunk similarity) and 2 (k topics) have no chunk/embedding table
# to operate on yet, so they are not exercised through the HTTP retrieval
# endpoint here -- there is no topic *selection* yet, only a direct crop+topic
# lookup. What plan-1:130-142 requires here is that they are decided and
# named; rule 3 (below) is additionally built and tested here, against
# constructed TopicCandidates, so real topic selection can call it unchanged
# once real MAX-similarity scores exist.


def test_k_defaults_to_3():
    """Rule 2: k = 3 topics, decided here (implemented once topic selection
    exists).

    Not exercised through the HTTP surface -- there is no topic *selection*
    to apply it to yet -- so this asserts the named constant directly.
    """
    assert retrieval.TOPIC_SELECTION_K == 3


def test_k_is_configurable_via_env_var():
    """Proved in a **subprocess**, not by `importlib.reload`-ing the shared
    module in this process: reload rebinds `TopicBudgetExceeded` to a new
    class object, but app/router/retrieval.py imported that class by value at
    its own import time -- so its `except TopicBudgetExceeded` would stop
    matching what a reloaded assemble_within_budget raises, corrupting every
    test after it with an unrelated HTTP 500. (Verified: reproduced exactly
    that failure while writing this test, tracked to this cause, and fixed by
    switching to the subprocess isolation tests/test_seed.py already uses for
    the same reason -- an import-time guard must not run in-process.)
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
    """Same subprocess-isolation reasoning as test_k_is_configurable_via_env_var
    above -- an import-time guard must not run in-process. Also pins the
    derived CONTEXT_CHAR_BUDGET (token budget * CHARS_PER_TOKEN), so a typo'd
    env var name silently falling back to the 8000-token default would be
    caught here rather than passing the suite silently.
    """
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
    """The exact-equality boundary for Rule 3 clause 1. plan-1:148 says drop
    "until it fits" -- fitting AT the budget is still fitting, not exceeding
    it. A `>` accidentally weakened to `>=` would drop something that never
    needed to go, and nothing else in this file would catch that: every other
    combination test here is comfortably under or over budget, never exactly on it.
    """
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
    """The exact-equality boundary for Rule 3 clause 2. plan-1:149 refuses
    only when a topic "exceeds" the budget -- landing exactly on it is not
    exceeding it. A `>` accidentally weakened to `>=` would refuse a topic
    that fits exactly, which nothing else in this file exercises: the other
    refusal tests use topics comfortably over budget, never exactly on it.
    """
    candidate = retrieval.TopicCandidate(
        topic="exact", score=1.0, documents=[_document(index=0, body="z" * 95)],
    )
    # len("doc 0") + 95 == 100 -- the budget below is set to that exact figure.
    assert candidate.context_chars == 100

    kept, dropped = retrieval.assemble_within_budget([candidate], budget=100)

    assert kept == [candidate]
    assert dropped == []


def test_assemble_within_budget_drops_whole_topics_lowest_score_first():
    """Rule 3 clause 1: when the combination exceeds the budget, drop whole
    topics, lowest-score-first, until it fits -- and name the dropped topics.

    Three topics, each individually well under the budget, whose COMBINATION
    exceeds it. The two lowest-scoring must be dropped, named, in ascending
    score order; the highest-scoring survives complete.
    """
    high = retrieval.TopicCandidate(topic="high", score=0.9, documents=[_document(body="h" * 40)])
    mid = retrieval.TopicCandidate(topic="mid", score=0.5, documents=[_document(body="m" * 40)])
    low = retrieval.TopicCandidate(topic="low", score=0.1, documents=[_document(body="l" * 40)])
    # Each candidate alone is 40 + len("doc 0") = 45 chars -- well under the
    # 50-char budget below, so none is refused individually. The combination
    # is 135, over budget; dropping only "low" leaves 90, still over budget;
    # dropping "mid" too leaves 45, which fits -- so both must go before the
    # loop stops with only "high" (45 <= 50) remaining.

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
    """Regression: the pre-fix code pre-checked every candidate for being
    individually oversized BEFORE any dropping was attempted, so a single
    huge, lowest-scoring topic caused a blanket refusal even though dropping
    it (it scores lowest anyway) would have let two healthy topics through.
    Two small, high-scoring topics that fit comfortably, plus one huge,
    lowest-scoring topic that alone exceeds the budget -- the huge one must
    be dropped, not cause every topic to be refused.
    """
    good_a = retrieval.TopicCandidate(topic="good-a", score=0.9, documents=[_document(body="a" * 20)])
    good_b = retrieval.TopicCandidate(topic="good-b", score=0.8, documents=[_document(body="b" * 20)])
    huge = retrieval.TopicCandidate(topic="huge", score=0.1, documents=[_document(body="h" * 1000)])
    # good_a/good_b: len("doc 0") + 20 == 25 chars each, well under the 100-char
    # budget below. huge alone: 5 + 1000 == 1005, alone exceeds the budget.
    assert good_a.context_chars == 25
    assert good_b.context_chars == 25
    assert huge.context_chars == 1005

    kept, dropped = retrieval.assemble_within_budget([good_a, good_b, huge], budget=100)

    assert [c.topic for c in kept] == ["good-a", "good-b"]
    assert [c.topic for c in dropped] == ["huge"]


def test_assemble_within_budget_drops_the_lower_scored_oversized_topic_then_refuses_the_survivor():
    """When BOTH candidates are individually oversized, the lower-scored one
    must be dropped first (Rule 3 clause 1 still applies to it too), and only
    THEN is the request refused -- citing the higher-scored survivor, never
    the already-dropped topic, and never a silent empty result.
    """
    high = retrieval.TopicCandidate(
        topic="high", score=0.9, documents=[_document(index=i, body="x" * 30) for i in range(5)],
    )
    low = retrieval.TopicCandidate(
        topic="low", score=0.5, documents=[_document(index=i, body="y" * 40) for i in range(5)],
    )
    # high alone: 5 * (len("doc N") + 30) == 175; low alone: 5 * (len("doc N") + 40) == 225.
    # Both exceed the 100-char budget below individually, low more so and
    # lower-scored -- it must be the one dropped first.
    assert high.context_chars == 175
    assert low.context_chars == 225

    with pytest.raises(retrieval.TopicBudgetExceeded) as excinfo:
        retrieval.assemble_within_budget([high, low], budget=100)

    assert excinfo.value.topic == "high"
    assert excinfo.value.document_count == 5


def test_an_oversized_topic_is_refused_with_413_naming_it_and_its_count(client):
    """HTTP-level equivalent of the refusal above, over the real endpoint:
    one topic whose combined document context alone exceeds the configured
    budget (CONTEXT_CHAR_BUDGET, default 32,000 chars) is refused (413),
    naming the topic and its document count -- never silently truncated.

    Twelve documents of 3,000 characters each assemble to 36,000+ characters
    of context, comfortably past the default budget, without needing to
    override any configuration -- so this exercises the real, deployed
    default rather than a value only a test ever sets.
    """
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
# Nothing normalized Item.topic anywhere before this fix: neither
# scripts/seed.py's `_get_or_create` (Item(**lookup, **defaults) + session.add())
# nor app/crud/item.py's create_item (model.Item(**body.model_dump())) passes
# through Pydantic -- both are real ORM constructions -- so a Pydantic
# field_validator on ItemBase alone would miss both. Item._normalize_topic
# (app/model/model.py), a SQLAlchemy @validates hook, fires on ORM
# attribute-set instead, which both paths go through.


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
    """scripts/seed.py's `_get_or_create` writes via `Item(**lookup, **defaults)`
    + `session.add()` -- real ORM construction, bypassing HTTP and Pydantic
    entirely. That is the corpus-generating path, so it must unify too."""
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
    """Honest limitation, not a hidden one: a row written before this fix
    existed -- simulated here via a Core-level `Item.__table__.insert()`,
    which bypasses the ORM entirely and therefore the `@validates` hook too
    -- is never retroactively normalized. A one-time backfill would be
    needed for any real legacy data; the actual dev database (`db`/`cms`)
    was checked this session and holds no non-normalized topic values today
    (`SELECT DISTINCT topic FROM items WHERE topic <> lower(btrim(topic))`
    returned zero rows), so no migration is written for a problem that does
    not yet exist.
    """
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
