"""Data access for member invites."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model
from app.auth.invite import generate_code, hash_code, normalise_email


def build_invite(
    role: str, expires_in_days: int, email: str | None
) -> tuple[model.MemberInvite, str]:
    """An unsaved invite and its plaintext code; shared with scripts/make_invite.py so both
    hash and normalise the same way."""
    code = generate_code()
    invite = model.MemberInvite(
        code_hash=hash_code(code),
        email=normalise_email(email) if email else None,
        role=role,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
    )
    return invite, code


async def create_invite(
    db: AsyncSession, role: str, expires_in_days: int, email: str | None
) -> tuple[model.MemberInvite, str]:
    """Add and flush the invite (for its id); the caller commits so the audit row shares
    the transaction. Returns (invite, plaintext code)."""
    invite, code = build_invite(role, expires_in_days, email)
    db.add(invite)
    await db.flush()
    return invite, code


async def claim_invite(db: AsyncSession, code: str, email: str) -> str | None:
    """Claim the invite for `code` and `email` (stripped, lower-cased); return its role or None.

    One atomic UPDATE (no race). Not committed: the row stays locked until the caller commits it
    with the member; a rollback un-claims it. A caller that gets None holds no lock."""
    result = await db.execute(
        text(
            "UPDATE member_invites SET used_at = now() "
            "WHERE code_hash = :h AND used_at IS NULL AND expires_at > now() "
            "AND (email IS NULL OR email = :e) RETURNING id, role"
        ),
        {"h": hash_code(code), "e": email},
    )
    row = result.one_or_none()
    return row.role if row else None


async def list_open_invites(db: AsyncSession, limit: int, offset: int) -> list[model.MemberInvite]:
    """Invites that can still be claimed (unused, unexpired), by id. Callers must serialise
    through a response model without code_hash."""
    result = await db.execute(
        select(model.MemberInvite)
        .where(model.MemberInvite.used_at.is_(None), model.MemberInvite.expires_at > func.now())
        .order_by(model.MemberInvite.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars())


async def lock_invite(db: AsyncSession, invite_id: int) -> model.MemberInvite | None:
    """The invite row locked FOR UPDATE (None if absent). A concurrent claim is one UPDATE on
    this row, so it waits and then matches nothing: we never delete a just-claimed row."""
    result = await db.execute(
        select(model.MemberInvite)
        .where(model.MemberInvite.id == invite_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()
