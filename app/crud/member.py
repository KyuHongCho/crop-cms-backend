"""Data access for members."""
import os

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model

def _read_budget(raw: str) -> int:
    # A negative value would trip the table's CHECK at signup, which the router
    # reports as a (false) duplicate email -- so refuse it here instead.
    value = int(raw)
    if value < 0:
        raise ValueError(f"TOKENS_BUDGET_DAILY must be >= 0, got {value}")
    return value


# Starting daily budget stamped on new members at signup; read once at import,
# so a change needs the container recreated. The per-member column is the
# authority afterwards; the model's server_default (20000) is only a fallback
# for inserts that bypass the ORM.
TOKENS_BUDGET_DAILY = _read_budget(os.environ.get("TOKENS_BUDGET_DAILY", "20000"))


async def get_member_by_email(db: AsyncSession, email: str) -> model.Member | None:
    result = await db.execute(select(model.Member).where(model.Member.email == email))
    return result.scalar_one_or_none()


async def get_member(db: AsyncSession, member_id: int) -> model.Member | None:
    return await db.get(model.Member, member_id)


async def lock_member_and_active_admins(
    db: AsyncSession, member_id: int
) -> tuple[model.Member | None, list[int]]:
    """Lock the member and every active admin in ONE statement, ordered by id.

    Returns (the member, or None if there is none; the ids of the active admins).
    The locks are held until the transaction ends. Locking the admins and then
    the member in two statements can deadlock: a promotion committed between
    them changes which admin rows the next request locks, so the two requests
    no longer lock in one global order. One ordered statement takes every lock
    in ascending id, so requests wait in a line and never in a cycle.

    A plain count of admins lets two admins who demote each other at once both
    see 2 and both succeed; with the lock the second waits, then sees 1.

    The session never expires objects (autoflush off, expire_on_commit off), so
    one loaded earlier in the request would be stale: populate_existing
    refreshes it from the locked row."""
    result = await db.execute(
        select(model.Member)
        .where(
            or_(
                and_(model.Member.role == "admin", model.Member.is_active),
                model.Member.id == member_id,
            )
        )
        .order_by(model.Member.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    rows = list(result.scalars().all())
    admin_ids = [row.id for row in rows if row.role == "admin" and row.is_active]
    return next((row for row in rows if row.id == member_id), None), admin_ids


async def actor_is_active_admin(db: AsyncSession, actor_id: int) -> bool:
    """Whether the actor is, right now, an active admin, read from the database
    with a FOR SHARE lock held to the end of the transaction: a concurrent demotion
    or deactivation (an UPDATE of that row) waits for the caller's commit. For
    handlers that touch no member row, so the whole-admin-set lock of
    lock_member_and_active_admins would be more than they need."""
    result = await db.execute(
        select(model.Member.id)
        .where(
            model.Member.id == actor_id,
            model.Member.role == "admin",
            model.Member.is_active.is_(True),
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none() is not None


async def list_members(
    db: AsyncSession,
    limit: int,
    offset: int,
    role: str | None = None,
    is_active: bool | None = None,
) -> list[model.Member]:
    query = select(model.Member).order_by(model.Member.id).limit(limit).offset(offset)
    if role is not None:
        query = query.where(model.Member.role == role)
    if is_active is not None:
        query = query.where(model.Member.is_active == is_active)
    result = await db.execute(query)
    return list(result.scalars().all())


async def create_member(
    db: AsyncSession,
    email: str,
    password_hash: str,
    display_name: str | None,
    role: str = "member",
) -> model.Member:
    member = model.Member(
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        role=role,
        tokens_budget_daily=TOKENS_BUDGET_DAILY,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member
