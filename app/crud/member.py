"""Data access for members."""
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model

# Starting daily budget stamped on new members at signup. The per-member
# column is the authority afterwards; the model's server_default (20000) is
# only a fallback for inserts that bypass the ORM.
def _read_budget(raw: str) -> int:
    # Fail loudly at import: a negative value would trip the table's CHECK at
    # signup, which the router reports as a (false) duplicate email.
    value = int(raw)
    if value < 0:
        raise ValueError(f"TOKENS_BUDGET_DAILY must be >= 0, got {value}")
    return value


TOKENS_BUDGET_DAILY = _read_budget(os.environ.get("TOKENS_BUDGET_DAILY", "20000"))


async def get_member_by_email(db: AsyncSession, email: str) -> model.Member | None:
    result = await db.execute(select(model.Member).where(model.Member.email == email))
    return result.scalar_one_or_none()


async def get_member(db: AsyncSession, member_id: int) -> model.Member | None:
    return await db.get(model.Member, member_id)


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
