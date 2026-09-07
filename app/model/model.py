from sqlalchemy import (
    Boolean, CheckConstraint, Column, ForeignKey, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import relationship

from app.db.db import Base

# The "Uncategorised" bucket. Deleting a sub-category refiles its documents here
# rather than destroying them, so this id is a schema-level fact, not a
# convention: app/db/migrate_db.py seeds both rows AND hard-codes the same
# number inside the PL/pgSQL trigger body, because a trigger cannot read a
# Python constant. Three homes, one number -- keep them in step. CI asserts the
# seeded ids equal these constants.
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

    # passive_deletes="all", not True. True suppresses only the pre-emptive
    # SELECT; a collection that happens to be loaded is still cascaded in
    # Python, which under ON DELETE RESTRICT means a NotNullViolation 500
    # instead of the database's own refusal. "all" hands the decision to
    # PostgreSQL unconditionally, so behaviour does not depend on what a
    # previous line in the same request happened to load.
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

    # No cascade="all, delete-orphan": it DELETEs the children in Python, which
    # is exactly the data loss this tree exists to prevent. Deleting a main
    # category that still holds sub-categories is refused -- by the router with
    # a count, and by ON DELETE RESTRICT underneath it. Re-adding delete-orphan
    # beside passive_deletes="all" is a configuration error SQLAlchemy refuses:
    #   ArgumentError: On MainCategory.subcategories, can't set
    #   passive_deletes='all' in conjunction with 'delete' or 'delete-orphan'
    #   cascade
    # Measured: it fires at configure_mappers(), which is LAZY -- the uvicorn
    # process still starts and GET / still answers 200. Every request that
    # touches a mapper is a 500 from then on, which is what CI's endpoint steps
    # catch; the start-up check alone would not.
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
    # Same reasoning as MainCategory.subcategories above -- but here the
    # database does not refuse: the BEFORE DELETE trigger installed by
    # app/db/migrate_db.py refiles the documents to UNCATEGORISED_SUB_CATEGORY_ID
    # first, so the RESTRICT has nothing left to refuse.
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
    )

    id = Column(Integer, primary_key=True)
    # RESTRICT, and deliberately NO server_default. Read alone this says
    # "deleting a sub-category is refused" -- it is not, and that is the one
    # cost of this design. The refiling is done first by the BEFORE DELETE
    # trigger refile_items_to_uncategorised() in app/db/migrate_db.py; RESTRICT
    # is what catches any path the trigger did not cover. A column default
    # would let ON DELETE SET DEFAULT do the same job, but a default also
    # applies at INSERT -- an INSERT omitting sub_category_id would silently
    # file the document under the bucket instead of being refused.
    sub_category_id = Column(
        Integer, ForeignKey("sub_categories.id", ondelete="RESTRICT"), nullable=False
    )
    # No trigger here: a crop with documents simply cannot be deleted. Crops are
    # seeded to match the advisor's data, not authored in this CMS.
    crop_id = Column(Integer, ForeignKey("crops.id", ondelete="RESTRICT"), nullable=False)

    # The shared question, e.g. "optimal-temperature". Retrieval returns the
    # whole set for a topic, so contradicting sources arrive together.
    topic = Column(String(128))
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
