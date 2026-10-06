from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, validates

from app.db.db import Base

# The "Uncategorised" bucket: where a deleted sub-category's documents are
# refiled to, instead of being destroyed. Its id lives in these constants and,
# hard-coded, in the seed SQL and trigger body under alembic/versions/, which
# build every fresh database and are frozen, so they do not import these
# constants (app/db/migrate_db.py's copies are built from them). CI checks the
# seeded ids still match these.
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
    # breaks NOT NULL and makes the delete a 500. No code path here loads them
    # eagerly, so True would look identical to "all" -- but "all" is what keeps
    # the delete correct once something does add a selectinload, by leaving the
    # decision to PostgreSQL either way.
    items = relationship("Item", back_populates="crop", passive_deletes="all")


class MainCategory(Base):
    """Kind of knowledge: crop profile, research literature, cultivation
    practice, pests and disorders. Retrieval does not use this tree: its unit
    is crop + topic (`Item.topic`). The seed is the exception: it files each
    crop under its own main category with a single "documents" sub-category,
    so the seeded tree repeats once per crop."""

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
    # As above -- except the database never has to refuse here: the trigger
    # refile_items_before_sub_category_delete refiles the documents first,
    # leaving RESTRICT nothing to block.
    items = relationship(
        "Item", back_populates="sub_category", passive_deletes="all",
    )


class Item(Base):
    """One narrative document with its provenance.

    Holds prose about agronomic figures, never the figures: bands live in the
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
        # Composite index on (crop_id, topic): retrieval's whole-topic-set
        # query (app/crud/retrieval.py:topic_set_statement) filters on
        # exactly this pair, so without it every retrieval request is a
        # sequential scan of `items`.
        Index("ix_items_crop_id_topic", "crop_id", "topic"),
    )

    id = Column(Integer, primary_key=True)
    # RESTRICT, and deliberately no default. This line alone reads as "deleting
    # a sub-category is refused" -- it is not; the BEFORE DELETE trigger
    # refile_items_before_sub_category_delete refiles the documents first, and
    # RESTRICT only catches what it missed.
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


# text-embedding-3-small's output size. Under pgvector's 2,000-dimension
# index cap, so an index can be added later without changing the column.
EMBEDDING_DIMENSIONS = 1536


class ItemChunk(Base):
    """One embedded slice of a document, written by scripts/reindex.py.

    Every document is embedded, published or not: publishing is a metadata
    flip, filtered at read time by the `published_item_chunks` view
    (alembic/versions/*_published_item_chunks_view.py). The chat layer reads
    that view, never this table.

    No relationship() to Item, deliberately: chunks are written by one
    script through Core statements, and ON DELETE CASCADE below removes them
    with their document without SQLAlchemy loading them.
    """

    __tablename__ = "item_chunks"
    __table_args__ = (UniqueConstraint("item_id", "chunk_index"),)

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    # sha256 hex of `content` -- the embedded text, title included, so a
    # title-only edit also re-embeds.
    content_hash = Column(String(64), nullable=False)
    embedding = Column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    # Per row, so a table holding vectors from two models is detectable.
    model = Column(String(64), nullable=False)
    # When the row was first inserted; a re-embed never changes it.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # When the current vector was produced: set on insert, reset by
    # scripts/reindex.py on every re-embed.
    embedded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# The roles a member can hold, least to most privileged. The CHECK on
# members.role below is built from it; the migration that adds the column writes
# it as a literal (a migration is frozen and does not import this).
MEMBER_ROLES = ("member", "editor", "admin")


class Member(Base):
    """A registered member. Everything in this app is per-member: chambers,
    grows and chat history all hang off this row."""

    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint("tokens_used_today >= 0", name="tokens_used_today_non_negative"),
        CheckConstraint("tokens_budget_daily >= 0", name="tokens_budget_daily_non_negative"),
        # Text + CHECK, not a native enum: a CHECK can be widened, and the new
        # value used, in one transaction; an enum value cannot.
        CheckConstraint(
            "role IN (" + ", ".join(f"'{role}'" for role in MEMBER_ROLES) + ")",
            name="role_valid",
        ),
    )

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True)
    # "password_hash", never "password": the column name is the only
    # documentation a reader of the schema gets that no plaintext is stored.
    password_hash = Column(Text, nullable=False)
    display_name = Column(String(64))

    # What the member may do beyond reading: see app/auth/dependency.py. Signup
    # never sets it (MemberCreate has no such field), so everyone starts as
    # "member"; an operator grants the first admin with SQL.
    role = Column(Text, nullable=False, server_default=text("'member'"))

    # An inactive member is meant to be refused everywhere (not yet enforced:
    # nothing reads this column until the deactivate slice).
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

    # Per-member daily model budget; see app/auth/budget.py (enforced by POST /chat).
    tokens_used_today = Column(Integer, nullable=False, server_default=text("0"))
    tokens_budget_daily = Column(Integer, nullable=False, server_default=text("20000"))
    budget_window_start = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))


class MemberAuditEvent(Base):
    """One administrative action on a member. Deliberately no foreign keys and
    no email: a record must outlive the member it is about, and keep no
    personal data after that member is deleted."""

    __tablename__ = "member_audit_events"

    id = Column(Integer, primary_key=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_id = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    target_id = Column(Integer)
    detail = Column(JSONB)
