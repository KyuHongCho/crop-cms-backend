"""Data access for members."""
import os

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model

def _read_budget(raw: str) -> int:
    # a negative value would trip the table CHECK at signup, which the router would
    # misreport as a duplicate email.
    value = int(raw)
    if value < 0:
        raise ValueError(f"TOKENS_BUDGET_DAILY must be >= 0, got {value}")
    return value


# starting daily budget stamped at signup; read once at import. The per-member column is
# the authority afterwards (the model's server_default is only a fallback).
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

    Else two admins demoting each other both see 2 and succeed; one id-ascending statement avoids a
    lock-order deadlock if a promotion lands between two. populate_existing refreshes unexpired objects.
    """
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
    """Whether the actor is, right now, an active admin, read FOR SHARE so a concurrent
    demotion waits for the caller's commit. For handlers touching no member row, where the
    whole-admin-set lock would be overkill."""
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
