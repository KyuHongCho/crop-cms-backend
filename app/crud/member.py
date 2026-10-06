"""Data access for members."""
import os

from sqlalchemy import select
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
    db: AsyncSession, email: str, password_hash: str, display_name: str | None
) -> model.Member:
    member = model.Member(
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        tokens_budget_daily=TOKENS_BUDGET_DAILY,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member
