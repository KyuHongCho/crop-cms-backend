from sqlalchemy import (
    Boolean, CheckConstraint, Column, ForeignKey, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import relationship

from app.db.db import Base


class Crop(Base):
    """A crop the advisor can be asked about. Identified the way the advisor
    identifies it: slug for lookup, ecocrop_id for the FAO data sheet."""

    __tablename__ = "crops"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), nullable=False, unique=True)          # "basil"
    common_name = Column(String(255), nullable=False)               # "basil"
    scientific_name = Column(String(255), nullable=False)           # "Ocimum basilicum"
    ecocrop_id = Column(Integer, unique=True)                       # 1547

    items = relationship("Item", back_populates="crop", passive_deletes=True)


class MainCategory(Base):
    """Kind of knowledge: crop profile, research literature, cultivation
    practice, pests and disorders. Crop is NOT a category -- it is an entity,
    so adding a crop does not duplicate this tree."""

    __tablename__ = "main_categories"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, server_default=text("0"))

    subcategories = relationship(
        "SubCategory", back_populates="main_category",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class SubCategory(Base):
    __tablename__ = "sub_categories"
    __table_args__ = (UniqueConstraint("main_category_id", "slug"),)

    id = Column(Integer, primary_key=True)
    main_category_id = Column(
        Integer, ForeignKey("main_categories.id", ondelete="CASCADE"), nullable=False
    )
    slug = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, server_default=text("0"))

    main_category = relationship("MainCategory", back_populates="subcategories")
    items = relationship(
        "Item", back_populates="sub_category",
        cascade="all, delete-orphan", passive_deletes=True,
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
    sub_category_id = Column(
        Integer, ForeignKey("sub_categories.id", ondelete="CASCADE"), nullable=False
    )
    crop_id = Column(Integer, ForeignKey("crops.id", ondelete="CASCADE"), nullable=False)

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
