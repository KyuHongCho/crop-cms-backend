"""Seed corpus tests.

Runs against db-test/cms_test like every other test here (conftest.py's
autouse _clean_database fixture TRUNCATEs first). scripts/seed.py never
imports crop_advisor: its seven FAO ECOCROP `optimal-temperature` documents
(one per crop) and the seven `ecocrop_id` values are pinned, along with basil's
two second-hand journal documents (Chang et al. 2005, Walters & Currey 2019),
to literal strings copied from crop_advisor/claims.py. The drift tests below
(one for basil, one per other crop) notice when the two diverge, but need a
*live* import of the sibling repo -- not always available to a developer who
has not checked out crop-climate-advisor (CI always has it; both checkouts run
before every other step).

That import is guarded with pytest.importorskip rather than a bare
`import`, because a failed bare import is a collection error: pytest exits
2 and reddens the *whole* file, including tests that need no such thing.
test_bare_advisor_import_would_fail_collection proves that failure mode
directly, and that the guard avoids it.
"""
import os
import pathlib
import subprocess
import sys

import pytest
from sqlalchemy import select

from app.model.model import Crop, Item
from scripts import seed

_ADVISOR_PATH = os.environ.get("ADVISOR_PATH")
if _ADVISOR_PATH and _ADVISOR_PATH not in sys.path:
    # append, not insert(0): insert(0) would put /advisor ahead of /src and the
    # stdlib -- guaranteed by list.insert's own semantics, not something
    # specific to this repo. Nothing collides only because the advisor's
    # scripts/ and tests/ lack __init__.py, so this repo's regular packages
    # win regardless of order -- a property of the advisor's layout, not one
    # this file asserts.
    sys.path.append(_ADVISOR_PATH)

claims = pytest.importorskip(
    "crop_advisor.claims",
    reason=(
        "crop-climate-advisor not available (ADVISOR_PATH unset, or the bind "
        "mount at that path is empty) -- see docker-compose.yaml"
    ),
)
ecocrop = pytest.importorskip("crop_advisor.ecocrop")


def _items_by_topic(db_session, topic: str, crop_slug: str = "basil") -> list[Item]:
    return list(
        db_session.execute(
            select(Item)
            .join(Crop, Crop.id == Item.crop_id)
            .where(Item.topic == topic, Crop.slug == crop_slug)
            .order_by(Item.id)
        ).scalars()
    )


def _items_for_crop(db_session, crop_slug: str) -> list[Item]:
    return list(
        db_session.execute(
            select(Item).join(Crop, Crop.id == Item.crop_id).where(Crop.slug == crop_slug)
        ).scalars()
    )


def test_basil_corpus_counts_per_topic(sync_db_session):
    """basil's corpus, counted exactly (the other crops are counted by their own test below).
    Published and unpublished are split so the DRAFT fixture cannot hide a missing document:
    the five RHS claims with no CC BY basil source were dropped, not stood in for."""
    seed.main()
    items = _items_for_crop(sync_db_session, "basil")
    published = {}
    for i in items:
        if i.published:
            published[i.topic] = published.get(i.topic, 0) + 1
    assert published == {"optimal-temperature": 3, "watering-needs": 3, "soil-ph": 1,
                         "pest-and-disease": 3, "propagation": 1}, sorted(i.title for i in items)
    unpublished = [i for i in items if not i.published]
    assert [(i.topic, i.source) for i in unpublished] == [("propagation", "Internal review notes")]
    assert len(items) == 12
    journal_sources = sorted(i.source for i in items if i.published and i.read_directly
                             and i.topic != "optimal-temperature" and i.source != seed.OFF_REGISTRY_SOURCE)
    assert journal_sources == sorted([
        "Driesen et al. (2021)", "Sayarer et al. (2023)", "Rahimi et al. (2023)",
        "Adamczyk-Szabela & Wolf (2022)", "Omer et al. (2021)", "Ben Naim et al. (2025)",
        "Walters & Lopez (2022)",
    ])
    assert not [i for i in items if "RHS" in i.source or "rhs.org" in i.url], (
        "basil documents must not cite the RHS web pages (not openly licensed)"
    )


def test_basil_propagation_document_says_it_is_not_about_cuttings(sync_db_session):
    """The one published propagation document covers light while raising seedlings from
    seed; it must say so, so it is not read as backing the dropped stem-cutting claim."""
    seed.main()
    (doc,) = [i for i in _items_by_topic(sync_db_session, "propagation") if i.published]
    assert doc.source == "Walters & Lopez (2022)"
    assert "not stem cuttings" in doc.title
    assert "NOT stem cuttings" in doc.condition and "from seed" in doc.condition


def test_seed_is_idempotent(sync_db_session):
    seed.main()
    first = sorted((i.title, i.topic, i.source) for i in sync_db_session.execute(select(Item)).scalars())

    seed.main()
    # seed.main() writes through its own session, so force this one to re-read
    # from the database rather than trust what it already holds in memory.
    sync_db_session.expire_all()
    second = sorted((i.title, i.topic, i.source) for i in sync_db_session.execute(select(Item)).scalars())

    assert first == second, "re-running the seed script changed or duplicated rows"


def test_seed_repairs_a_row_edited_outside_the_script(sync_db_session):
    """Re-running must bring an edited row back in line, not merely avoid
    duplicating it. test_seed_is_idempotent cannot see this: both of its runs
    write identical content, so an update branch that silently did nothing
    would look exactly like one that works.
    """
    seed.main()
    doc = sync_db_session.execute(select(Item)).scalars().first()
    doc_id, original_body = doc.id, doc.body

    doc.body = "DRIFTED -- edited outside the seed script"
    sync_db_session.commit()

    seed.main()
    sync_db_session.expire_all()
    assert sync_db_session.get(Item, doc_id).body == original_body, (
        "re-running the seed did not restore a row that had drifted"
    )


def test_three_temperature_documents_share_one_topic(sync_db_session):
    seed.main()
    docs = _items_by_topic(sync_db_session, "optimal-temperature")
    assert len(docs) == 3
    assert {d.topic for d in docs} == {"optimal-temperature"}
    assert all(d.published for d in docs)


def test_optimal_temperature_sources_match_the_live_registry_drift(sync_db_session):
    """The drift test proper. Fails when the test suite runs if
    scripts/seed.py's pinned literals stop matching what the advisor's
    registry actually returns."""
    seed.main()
    seeded_sources = sorted(d.source for d in _items_by_topic(sync_db_session, "optimal-temperature"))

    live_sources = sorted(c.source for c in claims.temperature_claims(ecocrop.load_crop("basil")))

    assert seeded_sources == live_sources


@pytest.mark.parametrize(
    "slug, ecocrop_id, per_topic",
    [
        ("lettuce", 1313, {"optimal-temperature": 1, "watering-needs": 2, "nutrient-solution": 3,
                           "pest-and-disease": 3, "propagation": 3}),
        ("strawberry", 1112, {"optimal-temperature": 1, "watering-needs": 1, "nutrient-solution": 1,
                              "pest-and-disease": 2, "propagation": 2}),
        # tomato and sweet pepper have no watering-needs document: all of theirs are about
        # solution composition (gaps stay absent).
        ("tomato", 1379, {"optimal-temperature": 1, "nutrient-solution": 3,
                          "pest-and-disease": 3, "propagation": 1}),
        ("cucumber", 817, {"optimal-temperature": 1, "watering-needs": 1, "nutrient-solution": 2,
                           "pest-and-disease": 2, "propagation": 1}),
        ("sweet-pepper", 618, {"optimal-temperature": 1, "nutrient-solution": 3,
                               "pest-and-disease": 3, "propagation": 1}),
        ("kale", 3867, {"optimal-temperature": 1, "watering-needs": 2,
                        "pest-and-disease": 3, "propagation": 2}),
    ],
)
def test_new_crop_pin_matches_the_live_registry(sync_db_session, slug, ecocrop_id, per_topic):
    """Per-crop drift test: the seeded Crop.ecocrop_id and the ECOCROP
    document's pinned source must equal what the advisor's registry returns."""
    seed.main()
    live_claims = claims.temperature_claims(ecocrop.load_crop(slug))
    live_ecocrop_sources = [c.source for c in live_claims if c.source.startswith("FAO ECOCROP")]

    crop = sync_db_session.execute(select(Crop).where(Crop.slug == slug)).scalar_one()
    assert crop.ecocrop_id == ecocrop_id
    assert f"FAO ECOCROP (id {crop.ecocrop_id})" in live_ecocrop_sources

    seeded_sources = sorted(d.source for d in _items_by_topic(sync_db_session, "optimal-temperature", slug))
    assert seeded_sources == sorted(c.source for c in live_claims)

    items = _items_for_crop(sync_db_session, slug)
    counts = {t: sum(1 for i in items if i.topic == t) for t in {i.topic for i in items}}
    assert counts == per_topic
    assert all(i.published and i.read_directly and i.via is None for i in items)


def test_licence_notes_use_the_shared_wording(sync_db_session):
    """Every ECOCROP document carries the shared FAO note and every journal document
    carries the shared CC BY note. The constants are also pinned by literal substrings
    (the FAO terms URL, "non-commercially", "CC BY 4.0", "changes were made"), so the
    wording cannot be edited away without this test noticing. It does not check that the
    wording is legally sufficient, only that these terms are still present."""
    assert "https://www.fao.org/contact-us/terms/en/" in seed._FAO_LICENCE_NOTE
    assert "non-commercially" in seed._FAO_LICENCE_NOTE
    assert "CC BY 4.0" in seed._CC_BY["licence_note"]
    assert "changes were made" in seed._CC_BY["licence_note"]

    seed.main()
    items = list(sync_db_session.execute(select(Item)).scalars())
    ecocrop_docs = [i for i in items if i.source.startswith("FAO ECOCROP")]
    assert len(ecocrop_docs) == len(seed._CROP_SPECS)
    assert all(i.licence_note == seed._FAO_LICENCE_NOTE for i in ecocrop_docs)

    # Journal documents: every non-basil crop's non-ECOCROP documents, plus basil's published
    # journal documents (basil's temperature papers are read via a secondary source, the folk
    # remedy and the DRAFT fixture are not journal documents).
    cc_by_slugs = [c["slug"] for c, _, _ in seed._CROP_SPECS if c["slug"] != "basil"]
    journal_docs = [
        i for slug in cc_by_slugs for i in _items_for_crop(sync_db_session, slug)
        if not i.source.startswith("FAO ECOCROP")
    ]
    basil_journal_docs = [
        i for i in _items_for_crop(sync_db_session, "basil")
        if i.topic != "optimal-temperature" and i.published and i.source != seed.OFF_REGISTRY_SOURCE
    ]
    assert len(basil_journal_docs) == 7
    journal_docs += basil_journal_docs
    assert all(i.licence_note == seed._CC_BY["licence_note"] for i in journal_docs)


def test_unpublished_draft_fixture_present(sync_db_session):
    seed.main()
    drafts = list(
        sync_db_session.execute(select(Item).where(Item.published.is_(False))).scalars()
    )
    assert len(drafts) == 1, f"expected exactly one unpublished draft, got {len(drafts)}"
    assert seed.DRAFT_BODY_MARKER in drafts[0].body


def test_one_document_source_not_in_the_registry(sync_db_session):
    seed.main()
    registry_sources = {c.source for c in claims.temperature_claims(ecocrop.load_crop("basil"))}
    assert seed.OFF_REGISTRY_SOURCE not in registry_sources

    items = list(sync_db_session.execute(select(Item)).scalars())
    off_registry = [i for i in items if i.source == seed.OFF_REGISTRY_SOURCE]
    assert len(off_registry) == 1, f"expected exactly one off-registry document, got {len(off_registry)}"


def test_bare_advisor_import_would_fail_collection():
    """Regression test for the failure mode this design avoids: a bare
    `import crop_advisor.claims`, collected with no ADVISOR_PATH on
    sys.path, makes pytest exit 2 -- a collection error that reddens the
    whole suite, not just the tests that need the advisor.

    Reproduces that directly in a subprocess with ADVISOR_PATH stripped,
    then asserts the *actual* guarded test_seed.py still collects as a
    clean skip (exit 5) under the same environment -- proving the
    importorskip guard is what prevents the failure demonstrated above.
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
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    # 5 == "no tests collected": pytest's own code for a clean module-level
    # skip (importorskip fires during collection). That is the guard working
    # as intended -- the failure mode it guards against is 2 ("Interrupted:
    # N errors during collection"), asserted above for the bare-import case.
    assert guarded.returncode == 5, (
        f"guarded test_seed.py did not collect as a clean skip with "
        f"ADVISOR_PATH unset (exit {guarded.returncode}, expected 5):\n"
        f"stdout={guarded.stdout}\nstderr={guarded.stderr}"
    )
