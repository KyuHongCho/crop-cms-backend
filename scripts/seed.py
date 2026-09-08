"""Seeds the basil corpus, pinned to the crop-climate-advisor registry.

A **script, not a migration**: `app/router/crop.py:15-17` is GET-only (crops
come from the advisor's ECOCROP data, not authored here) and there is no
author endpoint for crops at all, so this writes through the ORM directly
against the synchronous engine `app/db/migrate_db.py` already exposes.

The three `optimal-temperature` documents' `source` field is a **pinned
literal**, copied by hand from `crop_advisor/claims.py` -- this module never
imports `crop_advisor`. That is deliberate: pinning means a future edit to
the advisor's registry does not silently change what this CMS says about
basil. `tests/test_seed.py`'s drift test is what notices the two have
diverged, at seed time -- not this script. (`reference`/`condition`/`via`/
`url` are mostly, not always, exact copies -- see that test file's own notes;
they are not drift-tested.)

Idempotent: re-running updates existing rows to match this file rather than
duplicating them, keyed on (crop, title) for documents and on slug for the
category scaffold and the crop itself.
"""
from sqlalchemy.orm import Session

from app.db.migrate_db import engine as sync_engine
from app.model.model import Crop, Item, MainCategory, SubCategory

CROP = {
    "slug": "basil",
    "common_name": "basil",
    "scientific_name": "Ocimum basilicum",
    "ecocrop_id": 1547,
}

# The topic field, not the category tree, is what groups documents for
# retrieval (model.py:121-123 -- "retrieval returns the whole set for a
# topic"). One flat sub-category is enough here.
MAIN_CATEGORY = {"slug": "basil-content", "name": "Basil content"}
SUB_CATEGORY = {"slug": "documents", "name": "Documents"}

TOPIC_OPTIMAL_TEMPERATURE = "optimal-temperature"

# Distinctive marker so tests can find the draft by its body text, kept as
# a constant so nothing has to hand-copy the string.
DRAFT_BODY_MARKER = "INTERNAL-DRAFT-DO-NOT-PUBLISH-BASIL-PROPAGATION-NOTE"

# Deliberately absent from the advisor's registry, for tests that check
# registry membership.
OFF_REGISTRY_SOURCE = "Old Farmer's Almanac (folklore)"

# --- documents ---------------------------------------------------------------
# Each dict is passed straight to Item(**...); "topic", "published",
# "read_directly" default below where every document in a group agrees.

_TEMPERATURE_DOCS = [
    dict(
        title="FAO ECOCROP on basil's optimal temperature",
        body=(
            "The FAO ECOCROP data sheet for Ocimum basilicum (id 1547) states an "
            "optimal temperature range of 18-27 degrees C, bounded absolutely by "
            "7-36 degrees C. No cultivation condition is stated."
        ),
        source="FAO ECOCROP (id 1547)",
        reference="FAO ECOCROP data sheet, id 1547",
        url="https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=1547",
        read_directly=True,
        via=None,
        condition=None,
        licence_note=(
            "Non-commercial research use with attribution per FAO Terms and "
            "Conditions (https://www.fao.org/contact-us/terms/en/)."
        ),
    ),
    dict(
        title="Chang, Alderson & Wright (2005) on basil's optimal temperature",
        body=(
            "Chang, Alderson & Wright (2005) report an optimal range of 25-30 "
            "degrees C, under a stated daily light integral of 20-22 mol m-2 d-1. "
            "Read through Barickman et al. (2021), not from the original journal."
        ),
        source="Chang, Alderson & Wright (2005)",
        reference="Chang, Alderson & Wright (2005), J. Hortic. Sci. Biotechnol. 80:593-598",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC8226578/",
        read_directly=False,
        via="Barickman et al. (2021), Plants 10(6):1072",
        condition="DLI 20-22 mol m-2 d-1",
        licence_note=None,
    ),
    dict(
        title="Walters & Currey (2019) on basil's optimal temperature",
        body=(
            "Walters & Currey (2019) report a 29-35 degrees C optimum, "
            "conditioned on a stated daily light integral of 19.5 mol m-2 d-1. "
            "Does not agree with the ECOCROP band; both are published here and "
            "neither is ranked."
        ),
        source="Walters & Currey (2019)",
        reference="Walters & Currey (2019), HortScience 54(11):1915",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC10688745/",
        read_directly=False,
        via="Walters, Tarr & Lopez (2023), PLoS One 18(11):e0294905",
        condition="DLI 19.5 mol m-2 d-1",
        licence_note=None,
    ),
]

_WATERING_DOCS = [
    dict(
        title="General watering guidance for container-grown basil",
        body=(
            "Basil prefers evenly moist, free-draining soil; it tolerates brief "
            "dryness better than waterlogging, which quickly causes root rot."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
    dict(
        title="Signs of basil over- and under-watering",
        body=(
            "Yellowing lower leaves and a wilted-but-heavy pot point to "
            "overwatering; crisp, curling leaves on a light pot point to "
            "underwatering."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
    dict(
        title="Watering frequency versus season",
        body=(
            "Container basil typically needs water every 1-2 days in warm "
            "weather and considerably less once temperatures drop, since "
            "evaporative demand falls with them."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
]

_SOIL_PH_DOCS = [
    dict(
        title="Preferred soil pH range for basil",
        body=(
            "Basil grows best in slightly acidic to neutral soil, roughly pH "
            "6-7; outside that range nutrient uptake, particularly of iron and "
            "manganese, becomes less efficient."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
    dict(
        title="Amending soil pH before planting basil",
        body=(
            "Garden lime raises pH; sulphur or an acidifying fertiliser lowers "
            "it. Either is best applied weeks before planting, since soil pH "
            "shifts slowly."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
]

_PEST_DOCS = [
    dict(
        title="Common basil pests: aphids and Japanese beetles",
        body=(
            "Aphids cluster on new growth and are usually controlled with a "
            "strong water spray or insecticidal soap; Japanese beetles skeletonise "
            "leaves and are best hand-picked in the early morning."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
    dict(
        title="Fusarium wilt in basil",
        body=(
            "Fusarium wilt causes one-sided leaf yellowing and stem streaking; "
            "there is no cure once established, so resistant cultivars and clean "
            "seed are the practical defence."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
    dict(
        # Deliberately NOT a registry source -- see OFF_REGISTRY_SOURCE above.
        title="Folk remedy: basil planted beside tomatoes repels pests",
        body=(
            "Traditional companion-planting lore holds that basil grown beside "
            "tomatoes repels aphids and hornworms and improves tomato flavour. "
            "Unlike the temperature claims above, this is folk tradition, not a "
            "sourced study, and is recorded here as exactly that."
        ),
        source=OFF_REGISTRY_SOURCE,
        reference="Traditional companion-planting lore, as commonly repeated in gardening guides",
        # ItemBase.url has min_length=1 (app/schema/item.py) -- there being
        # no single citable source is exactly what "folklore" means here.
        url="(no single citable source -- oral/traditional)",
    ),
]

_PROPAGATION_DOCS = [
    dict(
        title="Propagating basil from stem cuttings",
        body=(
            "A 10cm stem cutting taken just below a leaf node roots readily in "
            "water within one to two weeks; pot on once roots reach a few "
            "centimetres."
        ),
        source="RHS Grow Your Own: Herbs",
        reference="Royal Horticultural Society, Grow Your Own: Herbs (basil)",
        url="https://www.rhs.org.uk/herbs/basil/grow-your-own",
    ),
    dict(
        # Unpublished draft fixture: published stays False, body carries
        # DRAFT_BODY_MARKER for draft-leak tests to find.
        title="DRAFT -- basil propagation, needs a second opinion before publishing",
        body=(
            f"{DRAFT_BODY_MARKER}: rooting hormone gel may speed root formation "
            "on stem cuttings, but this has not been checked against a second "
            "source yet -- do not publish until it has."
        ),
        source="Internal review notes",
        reference="Internal review notes (unpublished)",
        # ItemBase.url has min_length=1 (app/schema/item.py).
        url="(internal -- unpublished, no external URL yet)",
        published=False,
    ),
]

_TOPIC_GROUPS = [
    (TOPIC_OPTIMAL_TEMPERATURE, _TEMPERATURE_DOCS),
    ("watering-needs", _WATERING_DOCS),
    ("soil-ph", _SOIL_PH_DOCS),
    ("pest-and-disease", _PEST_DOCS),
    ("propagation", _PROPAGATION_DOCS),
]

_DOC_DEFAULTS = dict(
    published=True,
    read_directly=True,
    via=None,
    condition=None,
    licence_note=None,
)


def _get_or_create(session: Session, model, defaults: dict, **lookup):
    instance = session.query(model).filter_by(**lookup).one_or_none()
    if instance is not None:
        for key, value in defaults.items():
            setattr(instance, key, value)
        return instance
    instance = model(**lookup, **defaults)
    session.add(instance)
    session.flush()  # populate instance.id for callers that need it below
    return instance


def main() -> None:
    with Session(sync_engine) as session:
        crop = _get_or_create(
            session,
            Crop,
            {"common_name": CROP["common_name"], "scientific_name": CROP["scientific_name"],
             "ecocrop_id": CROP["ecocrop_id"]},
            slug=CROP["slug"],
        )
        main_category = _get_or_create(
            session, MainCategory, {"name": MAIN_CATEGORY["name"]}, slug=MAIN_CATEGORY["slug"],
        )
        sub_category = _get_or_create(
            session,
            SubCategory,
            {"name": SUB_CATEGORY["name"]},
            main_category_id=main_category.id,
            slug=SUB_CATEGORY["slug"],
        )

        for topic, docs in _TOPIC_GROUPS:
            for doc in docs:
                fields = {**_DOC_DEFAULTS, "topic": topic, **doc}
                title = fields.pop("title")
                source = fields.pop("source")
                fields["sub_category_id"] = sub_category.id
                # `source` is part of the key, not just a field to overwrite:
                # nothing makes (crop, title) unique, so without it this script
                # would treat somebody else's document with the same title as
                # its own and overwrite it.
                _get_or_create(
                    session,
                    Item,
                    fields,
                    crop_id=crop.id,
                    title=title,
                    source=source,
                )

        session.commit()


if __name__ == "__main__":
    main()
