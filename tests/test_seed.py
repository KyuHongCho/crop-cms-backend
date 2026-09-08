"""Seed corpus tests.

Runs against db-test/cms_test like every other test here (conftest.py's
autouse _clean_database fixture TRUNCATEs first). scripts/seed.py itself
never imports crop_advisor: its three `optimal-temperature` documents are
pinned to literal source strings copied from crop_advisor/claims.py. The
drift test below is what notices the two have diverged -- it needs a *live*
import of the sibling repo, which is not always available (a developer who
has not checked out crop-climate-advisor, or a CI runner before the sibling
checkout lands). That import is guarded with pytest.importorskip rather than
a bare `import`, because a bare import that fails is a collection error --
pytest exits 2 and reddens the *whole* file, including the tests below that
need no such thing. test_bare_advisor_import_would_fail_collection proves
that failure mode directly, and that the guard avoids it.
"""
import os
import subprocess
import sys

import pytest
from sqlalchemy import select

from app.model.model import Item
from scripts import seed

_ADVISOR_PATH = os.environ.get("ADVISOR_PATH")
if _ADVISOR_PATH and _ADVISOR_PATH not in sys.path:
    sys.path.insert(0, _ADVISOR_PATH)

claims = pytest.importorskip(
    "crop_advisor.claims",
    reason=(
        "crop-climate-advisor not available (ADVISOR_PATH unset, or the bind "
        "mount at that path is empty) -- see docker-compose.yaml"
    ),
)
load_crop = pytest.importorskip("crop_advisor.ecocrop").load_crop


def _items_by_topic(db_session, topic: str) -> list[Item]:
    return list(
        db_session.execute(
            select(Item).where(Item.topic == topic).order_by(Item.id)
        ).scalars()
    )


def test_seed_produces_12_to_15_documents_across_4_to_5_topics(sync_db_session):
    seed.main()
    items = list(sync_db_session.execute(select(Item)).scalars())
    topics = {item.topic for item in items}
    assert 12 <= len(items) <= 15, f"got {len(items)} documents: {[i.title for i in items]}"
    assert 4 <= len(topics) <= 5, f"got topics: {sorted(topics)}"


def test_seed_is_idempotent(sync_db_session):
    seed.main()
    first = sorted((i.title, i.topic, i.source) for i in sync_db_session.execute(select(Item)).scalars())

    seed.main()
    second = sorted((i.title, i.topic, i.source) for i in sync_db_session.execute(select(Item)).scalars())

    assert first == second, "re-running the seed script changed or duplicated rows"


def test_three_temperature_documents_share_one_topic(sync_db_session):
    seed.main()
    docs = _items_by_topic(sync_db_session, "optimal-temperature")
    assert len(docs) == 3
    assert {d.topic for d in docs} == {"optimal-temperature"}
    assert all(d.published for d in docs)


def test_optimal_temperature_sources_match_the_live_registry_drift(sync_db_session):
    """The drift test proper. Fails at seed time if scripts/seed.py's pinned
    literals stop matching what the advisor's registry actually returns."""
    seed.main()
    seeded_sources = sorted(d.source for d in _items_by_topic(sync_db_session, "optimal-temperature"))

    live_sources = sorted(c.source for c in claims.temperature_claims(load_crop("basil")))

    assert seeded_sources == live_sources


def test_unpublished_draft_fixture_present(sync_db_session):
    seed.main()
    drafts = list(
        sync_db_session.execute(select(Item).where(Item.published.is_(False))).scalars()
    )
    assert len(drafts) == 1, f"expected exactly one unpublished draft, got {len(drafts)}"
    assert seed.DRAFT_BODY_MARKER in drafts[0].body


def test_one_document_source_not_in_the_registry(sync_db_session):
    seed.main()
    registry_sources = {c.source for c in claims.temperature_claims(load_crop("basil"))}
    items = list(sync_db_session.execute(select(Item)).scalars())
    off_registry = [i for i in items if i.source not in registry_sources]
    assert len(off_registry) >= 1, "expected at least one document whose source is not in the registry"


def test_bare_advisor_import_would_fail_collection():
    """Regression test for the exact incident this design avoids: a bare
    `import crop_advisor.claims` inside a test file, collected with no
    ADVISOR_PATH on sys.path, makes pytest exit 2 -- a collection error that
    reddens the whole suite, not just the tests that need the advisor.

    Reproduces it directly in a subprocess with ADVISOR_PATH stripped, then
    asserts the *actual* guarded test_seed.py still collects clean (exit 0)
    under the same stripped environment -- proving the importorskip guard is
    what prevents the failure this test demonstrates.
    """
    env = {k: v for k, v in os.environ.items() if k != "ADVISOR_PATH"}

    bare_probe = "import crop_advisor.claims\n"
    bare = subprocess.run(
        [sys.executable, "-c", bare_probe],
        env=env,
        capture_output=True,
        text=True,
    )
    assert bare.returncode != 0, (
        "expected a bare import of crop_advisor.claims to fail with no "
        f"ADVISOR_PATH on sys.path; it unexpectedly succeeded: {bare.stdout}"
    )

    guarded = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_seed.py"],
        env=env,
        capture_output=True,
        text=True,
        cwd="/src",
    )
    # 5 == "no tests collected": pytest's own code for a clean module-level
    # skip (importorskip fires during collection). That is the guard working
    # as intended -- the failure mode this test guards against is 2
    # ("Interrupted: N errors during collection"), asserted against directly
    # above for the bare-import case.
    assert guarded.returncode == 5, (
        f"guarded test_seed.py did not collect as a clean skip with "
        f"ADVISOR_PATH unset (exit {guarded.returncode}, expected 5):\n"
        f"stdout={guarded.stdout}\nstderr={guarded.stderr}"
    )
