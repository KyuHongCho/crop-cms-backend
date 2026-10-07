"""Per-member daily token budget, enforced by POST /chat (app/router/chat.py).

SQL arithmetic (Python read-modify-write races); no lock, so overshoot is possible: keep a provider
spend limit, and turn its limit error into a clean failure. Days follow the DB's CURRENT_DATE.
"""
from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model
from app.auth.dependency import get_current_member
from app.db.db import get_db

_RESET_STALE_WINDOW = text(
    "UPDATE members SET tokens_used_today = 0, budget_window_start = CURRENT_DATE "
    "WHERE id = :id AND budget_window_start < CURRENT_DATE"
)
_READ_BUDGET = text(
    "SELECT tokens_used_today, tokens_budget_daily, "
    "       CEIL(EXTRACT(EPOCH FROM ((CURRENT_DATE + 1)::timestamp - LOCALTIMESTAMP)))::int "
    "         AS seconds_to_reset "
    "FROM members WHERE id = :id"
)
_ADD_USAGE = text(
    "UPDATE members SET tokens_used_today = tokens_used_today + :n WHERE id = :id"
)


async def check_budget(db: AsyncSession, member_id: int) -> None:
    """Reset the member's window if from a previous day, then raise 429 (Retry-After in
    seconds until rollover) if at or over budget. Commits the reset with the check."""
    await db.execute(_RESET_STALE_WINDOW, {"id": member_id})
    row = (await db.execute(_READ_BUDGET, {"id": member_id})).one_or_none()
    await db.commit()
    if row is None:
        # deleted after get_current_member passed: the guard's 401, not a 500.
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if row.tokens_used_today >= row.tokens_budget_daily:
        raise HTTPException(
            status_code=429,
            detail="Daily token budget exhausted",
            headers={"Retry-After": str(max(row.seconds_to_reset, 1))},
        )


async def record_usage(db: AsyncSession, member_id: int, tokens: int) -> None:
    """Add the provider-reported usage with an atomic UPDATE."""
    if tokens < 0:
        raise ValueError("tokens must be >= 0")
    await db.execute(_ADD_USAGE, {"id": member_id, "n": tokens})
    await db.commit()


async def require_budget(
    member: model.Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
) -> model.Member:
    """Dependency: the authenticated member, budget checked. Use instead of
    get_current_member for any route that calls a model. Budget columns are re-read after the
    check; after record_usage the in-memory object is stale again."""
    await check_budget(db, member.id)
    await db.refresh(member, ["tokens_used_today", "budget_window_start"])
    return member
