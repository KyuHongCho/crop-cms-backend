"""Per-member daily token budget, enforced by POST /chat (app/router/chat.py).

This is the per-member control. The overall spend bound is the monthly spend
limit set in each provider's console (Billing page); it applies whatever this
module does, and a provider that has reached its limit answers with an error
the chat handler must turn into a clean failure for the member.

A model-calling handler does:

    member = Depends(get_current_member)
    await check_budget(db, member.id)     # 429 before any model call
    ...call the model...
    await record_usage(db, member.id, usage.input_tokens + usage.output_tokens)

(`require_budget` below is the same check as a dependency; POST /chat calls
`check_budget` directly.) `check_budget` resets a stale window and refuses an
exhausted member.
`record_usage` adds what the provider *reported*, not an estimate. Both do
their arithmetic in SQL: a read-modify-write in Python races between two
concurrent requests from the same member.

Check and record are separate steps with no lock, so concurrent requests from
one member can overshoot the cap, and the last request can push usage past the
budget; this is inherent to checking before the call and adding the reported
usage after.

Day boundaries are the database's CURRENT_DATE (its session time zone), the
same clock the column default uses.
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
    """Reset the member's window if it is from a previous day, then raise 429
    (with Retry-After, in seconds until the window rolls over) if they are at
    or over budget. Commits the reset in the same transaction as the check."""
    await db.execute(_RESET_STALE_WINDOW, {"id": member_id})
    row = (await db.execute(_READ_BUDGET, {"id": member_id})).one_or_none()
    await db.commit()
    if row is None:
        # Deleted (DELETE /members/{id}) after get_current_member passed: the same
        # 401 that guard gives on the member's next request, not a 500.
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
    """Dependency: the authenticated member, with budget checked. Depend on
    this instead of get_current_member for any route that calls a model.

    The member's budget columns are re-read after the check, so the handler
    sees post-reset values. Known limit: after record_usage the in-memory
    object is stale again."""
    await check_budget(db, member.id)
    await db.refresh(member, ["tokens_used_today", "budget_window_start"])
    return member
