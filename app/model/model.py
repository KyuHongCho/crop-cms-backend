from sqlalchemy import (
    Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import relationship, validates

from app.db.db import Base

# The "Uncategorised" bucket: where a deleted sub-category's documents are
# refiled to, instead of being destroyed. app/db/migrate_db.py writes this same
# number into the seeded rows and into the trigger, which cannot read a Python
# constant. CI checks the seeded ids still match these.
UNCATEGORISED_MAIN_CATEGORY_ID = 1
UNCATEGORISED_SUB_CATEGORY_ID = 1


class Crop(Base):
    """A crop the advisor can be asked about. Identified the way the advisor
    identifies it: slug for lookup, ecocrop_id for the FAO data sheet."""

    __tablename__ = "crops"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), nullable=False, unique=True)          # "basil"
    common_name = Column(String(255), nullable=False)               # "basil"
    scientific_name = Column(String(255), nullable=False)           # "Ocimum basilicum"
    ecocrop_id = Column(Integer, unique=True)                       # 1547

    # "all", not True. True only skips the look-ahead SELECT; it does not stop
    # SQLAlchemy blanking the foreign key of documents already in memory, which
    # breaks NOT NULL and makes the delete a 500. Nothing loads them today, so
    # True would look fine -- "all" is what keeps it fine after someone adds a
    # selectinload, by leaving the decision to PostgreSQL either way.
    items = relationship("Item", back_populates="crop", passive_deletes="all")


class MainCategory(Base):
    """Kind of knowledge: crop profile, research literature, cultivation
    practice, pests and disorders. Crop is NOT a category -- it is an entity,
    so adding a crop does not duplicate this tree."""

    __tablename__ = "main_categories"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, server_default=text("0"))

    # No cascade="all, delete-orphan": it deletes the children in Python, which
    # is the data loss this design exists to prevent. Deleting a main category
    # that still holds sub-categories is refused instead -- by the router, with
    # a count, and by ON DELETE RESTRICT beneath it.
    #
    # Putting delete-orphan back alongside passive_deletes="all" raises
    # ArgumentError, but not at start-up: mappers configure lazily, so the app
    # boots and GET / still answers 200 while every request that touches the
    # database returns 500. CI catches it on the endpoint steps, not the
    # start-up check.
    subcategories = relationship(
        "SubCategory", back_populates="main_category", passive_deletes="all",
    )


class SubCategory(Base):
    __tablename__ = "sub_categories"
    __table_args__ = (UniqueConstraint("main_category_id", "slug"),)

    id = Column(Integer, primary_key=True)
    main_category_id = Column(
        Integer, ForeignKey("main_categories.id", ondelete="RESTRICT"), nullable=False
    )
    slug = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, server_default=text("0"))

    main_category = relationship("MainCategory", back_populates="subcategories")
    # As above -- except the database never has to refuse here: the trigger in
    # app/db/migrate_db.py refiles the documents to the bucket first, leaving
    # RESTRICT nothing to block.
    items = relationship(
        "Item", back_populates="sub_category", passive_deletes="all",
    )


class Item(Base):
    """One narrative document with its provenance.

    Holds prose ABOUT agronomic figures, never the figures: bands live in the
    advisor's ECOCROP data and claims module.

    There is deliberately no priority, rank or is_primary column. Documents that
    contradict each other are all publishable, and retrieval returns every
    document sharing a `topic` rather than a top-k slice -- otherwise a LIMIT
    silently picks a winner among disagreeing sources.
    """

    __tablename__ = "items"
    __table_args__ = (
        # Mirrors crop_advisor/claims.py: a claim read first-hand cannot also
        # name the paper it was read through.
        CheckConstraint(
            "NOT (read_directly AND coalesce(btrim(via), '') <> '')",
            name="read_directly_excludes_via",
        ),
        # `items_pkey` was the only index before this slice (verified via
        # `\d items`). Retrieval's whole-topic-set query filters on exactly
        # this pair -- app/crud/retrieval.py:topic_set_statement -- so without
        # it every retrieval request is a sequential scan of `items`.
        Index("ix_items_crop_id_topic", "crop_id", "topic"),
    )

    id = Column(Integer, primary_key=True)
    # RESTRICT, and deliberately no default. This line alone reads as "deleting
    # a sub-category is refused" -- it is not; the BEFORE DELETE trigger
    # refile_items_before_sub_category_delete (app/db/migrate_db.py) refiles the
    # documents first, and RESTRICT only catches what it missed.
    # ON DELETE SET DEFAULT would refile without a trigger, but it needs a
    # column default, and defaults apply on INSERT too: a new document missing
    # sub_category_id would be filed under the bucket instead of rejected.
    sub_category_id = Column(
        Integer, ForeignKey("sub_categories.id", ondelete="RESTRICT"), nullable=False
    )
    # No trigger here: a crop that still has documents simply cannot be deleted.
    crop_id = Column(Integer, ForeignKey("crops.id", ondelete="RESTRICT"), nullable=False)

    # The shared question, e.g. "optimal-temperature". Retrieval returns the
    # whole set for a topic, so contradicting sources arrive together.
    topic = Column(String(128))

    @validates("topic")
    def _normalize_topic(self, key, value):
        """Without this, 'optimal-temperature' and 'Optimal-Temperature' form
        two silently disjoint topic sets -- see app/crud/retrieval.py's
        exact-match query. Fires on ORM attribute-set, covering both real
        write paths: app/crud/item.py's create_item() and scripts/seed.py's
        _get_or_create()."""
        if value is None:
            return value
        return value.strip().lower()

    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    published = Column(Boolean, nullable=False, server_default=text("false"))

    # Provenance, mirroring crop_advisor/claims.py.
    source = Column(String(255), nullable=False)      # "Walters & Currey (2019)"
    reference = Column(Text, nullable=False)          # full bibliographic entry
    url = Column(Text, nullable=False)                # where it can be checked
    read_directly = Column(Boolean, nullable=False)
    via = Column(Text)                                # paper it was read through
    condition = Column(Text)                          # "at DLI 19.5 mol m-2 d-1"
    licence_note = Column(Text)                       # e.g. FAO attribution terms

    crop = relationship("Crop", back_populates="items")
    sub_category = relationship("SubCategory", back_populates="items")
