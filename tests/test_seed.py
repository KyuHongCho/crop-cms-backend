"""Seed corpus tests (db-test/cms_test; conftest's autouse _clean_database TRUNCATEs first).

The drift tests need a live import of the advisor repo, guarded by pytest.importorskip: a bare
import failing is a collection error (exit 2) that reddens the whole file. CI always has it.
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
    # append, not insert(0), which would put /advisor ahead of /src and the stdlib. Nothing
    # collides only because the advisor's scripts/ and tests/ lack __init__.py.
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
    """basil's corpus, counted exactly. Published and unpublished are split so the DRAFT fixture
    cannot hide a missing document: the five RHS claims with no CC BY source were dropped."""
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
    """The one published propagation document covers raising seedlings from seed and must say
    so, so it is not read as backing the dropped stem-cutting claim."""
    seed.main()
    (doc,) = [i for i in _items_by_topic(sync_db_session, "propagation") if i.published]
    assert doc.source == "Walters & Lopez (2022)"
    assert "not stem cuttings" in doc.title
    assert "NOT stem cuttings" in doc.condition and "from seed" in doc.condition


def test_seed_is_idempotent(sync_db_session):
    seed.main()
    first = sorted((i.title, i.topic, i.source) for i in sync_db_session.execute(select(Item)).scalars())

    seed.main()
    # seed.main() writes through its own session; force this one to re-read from the database.
    sync_db_session.expire_all()
    second = sorted((i.title, i.topic, i.source) for i in sync_db_session.execute(select(Item)).scalars())

    assert first == second, "re-running the seed script changed or duplicated rows"


def test_seed_repairs_a_row_edited_outside_the_script(sync_db_session):
    """Re-running must bring an edited row back in line, not merely avoid duplicating it.
    test_seed_is_idempotent cannot see this: a do-nothing update branch looks identical there.
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
    """The drift test proper: fails when scripts/seed.py's pinned literals stop matching the
    advisor's registry."""
    seed.main()
    seeded_sources = sorted(d.source for d in _items_by_topic(sync_db_session, "optimal-temperature"))

    live_sources = sorted(c.source for c in claims.temperature_claims(ecocrop.load_crop("basil")))

    assert seeded_sources == live_sources


def test_second_hand_basil_via_and_url_match_the_live_advisor_claims_drift(sync_db_session):
    """Drift check on the two second-hand basil documents: seeded `via` and `url` equal the
    live advisor claim's. CI checks the advisor out unpinned, so this follows its default branch."""
    seed.main()
    seeded = {d.source: d for d in _items_by_topic(sync_db_session, "optimal-temperature")}
    live = {c.source: c for c in claims.JOURNAL_TEMPERATURE_CLAIMS}
    assert sorted(live) == ["Chang, Alderson & Wright (2005)", "Walters & Currey (2019)"]
    for source, claim in live.items():
        assert seeded[source].via == claim.via, source
        assert seeded[source].url == claim.url, source


@pytest.mark.parametrize(
    "slug, ecocrop_id, per_topic",
    [
        ("lettuce", 1313, {"optimal-temperature": 1, "watering-needs": 2, "nutrient-solution": 3,
                           "pest-and-disease": 3, "propagation": 3}),
        ("strawberry", 1112, {"optimal-temperature": 1, "watering-needs": 1, "nutrient-solution": 1,
                              "pest-and-disease": 2, "propagation": 2}),
        # tomato and sweet pepper have no watering-needs document
        # (all theirs are about solution composition).
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
    """Per-crop drift test: Crop.ecocrop_id and the ECOCROP document's source equal the registry's."""
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
    """Every ECOCROP document carries the shared FAO note and every journal document the CC BY
    note, pinned by literal substrings so the wording cannot be edited away unnoticed (not that
    it is legally sufficient)."""
    assert "https://www.fao.org/contact-us/terms/en/" in seed._FAO_LICENCE_NOTE
    assert "non-commercially" in seed._FAO_LICENCE_NOTE
    assert "CC BY 4.0" in seed._CC_BY["licence_note"]
    assert "changes were made" in seed._CC_BY["licence_note"]

    seed.main()
    items = list(sync_db_session.execute(select(Item)).scalars())
    ecocrop_docs = [i for i in items if i.source.startswith("FAO ECOCROP")]
    assert len(ecocrop_docs) == len(seed._CROP_SPECS)
    assert all(i.licence_note == seed._FAO_LICENCE_NOTE for i in ecocrop_docs)

    # journal documents: non-ECOCROP documents of every non-basil crop, plus basil's published
    # journal ones (its temperature papers are second-hand; folk remedy and DRAFT are not journals).
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

    # the two second-hand basil documents carry the shared "via" note, not the direct-read CC BY one.
    via_note = seed._CC_BY_VIA_NOTE
    assert "https://creativecommons.org/licenses/by/4.0/" in via_note
    assert "changes were made" in via_note
    assert "Walters, Tarr & Lopez (2023)" in via_note
    assert ("The original papers (Chang et al. 2005; Walters & Currey 2019) were not read "
            "and their own licences were not checked.") in via_note
    via_docs = [
        i for i in _items_for_crop(sync_db_session, "basil")
        if i.topic == "optimal-temperature" and not i.read_directly
    ]
    assert sorted(i.source for i in via_docs) == ["Chang, Alderson & Wright (2005)",
                                                  "Walters & Currey (2019)"]
    for i in via_docs:
        assert i.licence_note == via_note
        assert "https://creativecommons.org/licenses/by/4.0/" in i.licence_note
        assert "changes were made" in i.licence_note
        assert "Walters, Tarr & Lopez (2023)" in i.licence_note


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
    """Regression test: a bare `import crop_advisor.claims` with no ADVISOR_PATH makes pytest exit 2
    (collection error, whole suite). Reproduced in a subprocess, then asserts the guarded
    test_seed.py collects as a clean skip (exit 5), proving importorskip is what prevents it.
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
    # 5 == "no tests collected": pytest's code for a clean module-level skip; the failure
    # mode guarded against is 2 (collection errors).
    assert guarded.returncode == 5, (
        f"guarded test_seed.py did not collect as a clean skip with "
        f"ADVISOR_PATH unset (exit {guarded.returncode}, expected 5):\n"
        f"stdout={guarded.stdout}\nstderr={guarded.stderr}"
    )
