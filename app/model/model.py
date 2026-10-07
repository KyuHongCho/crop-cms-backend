from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship, validates

from app.db.db import Base

# refiled-to bucket for a deleted sub-category's documents. The alembic seed SQL and
# trigger hard-code these ids (migrations are frozen); CI checks they still match.
UNCATEGORISED_MAIN_CATEGORY_ID = 1
UNCATEGORISED_SUB_CATEGORY_ID = 1


class Crop(Base):
    """A crop the advisor can be asked about: slug for lookup, ecocrop_id for the FAO data sheet."""

    __tablename__ = "crops"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), nullable=False, unique=True)
    common_name = Column(String(255), nullable=False)
    scientific_name = Column(String(255), nullable=False)
    ecocrop_id = Column(Integer, unique=True)

    # "all", not True: True still lets SQLAlchemy blank the FK of in-memory documents,
    # breaking NOT NULL (500). "all" leaves the delete to PostgreSQL even once something
    # adds a selectinload.
    items = relationship("Item", back_populates="crop", passive_deletes="all")


class MainCategory(Base):
    """Kind of knowledge: crop profile, literature, cultivation practice, pests.
    Retrieval does not use this tree (unit is crop + topic); the seed files each crop
    under its own main category, so the seeded tree repeats per crop."""

    __tablename__ = "main_categories"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, server_default=text("0"))

    # No delete-orphan: it deletes children in Python (the data loss to prevent), and
    # with passive_deletes="all" it raises ArgumentError lazily -- the app boots, GET /
    # answers 200, every DB request 500s. Non-empty deletes are refused by router + RESTRICT.
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
    # the trigger refile_items_before_sub_category_delete refiles documents first,
    # so RESTRICT never has to refuse here.
    items = relationship(
        "Item", back_populates="sub_category", passive_deletes="all",
    )


class Item(Base):
    """One narrative document with its provenance (prose only; figures live in the advisor's data).

    No priority/rank/is_primary column on purpose: contradicting documents are all
    publishable, and retrieval returns every document of a `topic`, not a top-k slice.
    """

    __tablename__ = "items"
    __table_args__ = (
        # mirrors crop_advisor/claims.py: a first-hand claim cannot name a via paper.
        CheckConstraint(
            "NOT (read_directly AND coalesce(btrim(via), '') <> '')",
            name="read_directly_excludes_via",
        ),
        # retrieval's whole-topic query (app/crud/retrieval.py:topic_set_statement)
        # filters on exactly this pair; without it, a sequential scan of `items`.
        Index("ix_items_crop_id_topic", "crop_id", "topic"),
    )

    id = Column(Integer, primary_key=True)
    # RESTRICT, no default: the BEFORE DELETE trigger refiles documents first and RESTRICT
    # only catches misses. SET DEFAULT would need a column default, which also applies on
    # INSERT, filing a document with no sub_category_id under the bucket instead of rejecting.
    sub_category_id = Column(
        Integer, ForeignKey("sub_categories.id", ondelete="RESTRICT"), nullable=False
    )
    # No trigger here: a crop that still has documents simply cannot be deleted.
    crop_id = Column(Integer, ForeignKey("crops.id", ondelete="RESTRICT"), nullable=False)

    # the shared question, e.g. "optimal-temperature"; retrieval returns the whole set.
    topic = Column(String(128))

    @validates("topic")
    def _normalize_topic(self, key, value):
        """Stops 'optimal-temperature' and 'Optimal-Temperature' forming disjoint topic
        sets (retrieval matches exactly). Fires on attribute-set, covering create_item()
        and scripts/seed.py's _get_or_create()."""
        if value is None:
            return value
        return value.strip().lower()

    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    published = Column(Boolean, nullable=False, server_default=text("false"))

    # provenance, mirroring crop_advisor/claims.py.
    source = Column(String(255), nullable=False)
    reference = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    read_directly = Column(Boolean, nullable=False)
    via = Column(Text)                                # paper it was read through
    condition = Column(Text)                          # "at DLI 19.5 mol m-2 d-1"
    licence_note = Column(Text)                       # e.g. FAO attribution terms

    crop = relationship("Crop", back_populates="items")
    sub_category = relationship("SubCategory", back_populates="items")


# text-embedding-3-small output size; under pgvector's 2,000-dim index cap so an index can be added later.
EMBEDDING_DIMENSIONS = 1536


class ItemChunk(Base):
    """One embedded slice of a document, written by scripts/reindex.py.

    The chat layer reads the `published_item_chunks` view, never this table. No relationship() to
    Item: chunks are written via Core; ON DELETE CASCADE removes them without loading.
    """

    __tablename__ = "item_chunks"
    __table_args__ = (UniqueConstraint("item_id", "chunk_index"),)

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    # sha256 of `content` (title included) so a title-only edit also re-embeds.
    content_hash = Column(String(64), nullable=False)
    embedding = Column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    # per row, so vectors from two models are detectable.
    model = Column(String(64), nullable=False)
    # first insert; a re-embed never changes it.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # when the current vector was produced; reset by reindex on every re-embed.
    embedded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


# least to most privileged; the members.role CHECK is built from it (the migration writes a literal).
MEMBER_ROLES = ("member", "editor", "admin")


class Member(Base):
    """A registered member; chambers, grows and chat history all hang off this row."""

    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint("tokens_used_today >= 0", name="tokens_used_today_non_negative"),
        CheckConstraint("tokens_budget_daily >= 0", name="tokens_budget_daily_non_negative"),
        # Text + CHECK, not a native enum: a CHECK widens in one transaction, an enum does not.
        CheckConstraint(
            "role IN (" + ", ".join(f"'{role}'" for role in MEMBER_ROLES) + ")",
            name="role_valid",
        ),
    )

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, unique=True)
    # "password_hash", never "password": the name itself documents that no plaintext is stored.
    password_hash = Column(Text, nullable=False)
    display_name = Column(String(64))

    # signup never sets it (MemberCreate has no such field); the first admin is granted
    # by an operator with SQL.
    role = Column(Text, nullable=False, server_default=text("'member'"))

    # inactive members are refused everywhere (get_current_member, and login with the same
    # 401 as a wrong password), read per request.
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

    # daily model budget, enforced by POST /chat (app/auth/budget.py).
    tokens_used_today = Column(Integer, nullable=False, server_default=text("0"))
    tokens_budget_daily = Column(Integer, nullable=False, server_default=text("20000"))
    budget_window_start = Column(Date, nullable=False, server_default=text("CURRENT_DATE"))


class MemberAuditEvent(Base):
    """One administrative action on a member. No foreign keys and no email, so the record
    outlives the member and keeps no personal data after deletion."""

    __tablename__ = "member_audit_events"

    id = Column(Integer, primary_key=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_id = Column(Integer, nullable=False)
    action = Column(Text, nullable=False)
    target_id = Column(Integer)
    detail = Column(JSONB)


# never "admin": the first admin is promoted with SQL, so a leaked invite cannot mint one.
INVITE_ROLES = ("member", "editor")


class MemberInvite(Base):
    """A one-time signup invitation. Only the SHA-256 of the code is stored (the code is
    shown once). No foreign keys: like member_audit_events, a row outlives its people."""

    __tablename__ = "member_invites"
    __table_args__ = (
        CheckConstraint(
            "role IN (" + ", ".join(f"'{role}'" for role in INVITE_ROLES) + ")",
            name="invite_role_valid",
        ),
    )

    id = Column(Integer, primary_key=True)
    code_hash = Column(String(64), nullable=False, unique=True)
    # when set, only a signup with this (lower-cased) email may claim the invite.
    email = Column(String(255))
    role = Column(Text, nullable=False, server_default=text("'member'"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # NULL until claimed; the claim is one atomic UPDATE ... WHERE used_at IS NULL.
    used_at = Column(DateTime(timezone=True))


class LoginThrottle(Base):
    """Failed-login counter, one row per account (app/auth/throttle.py). The key is a SHA-256 of
    the normalised email, so unknown addresses leave no personal data. No foreign keys."""

    __tablename__ = "login_throttle"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_login_throttle_window_started_at", "window_started_at"),
    )

    email_key = Column(String(64), primary_key=True)
    attempts = Column(Integer, nullable=False)
    window_started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # new on every window restart: a release only applies to the window it reserved in.
    window_id = Column(UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"))
