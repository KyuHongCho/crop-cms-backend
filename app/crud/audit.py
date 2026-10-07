"""Audit trail for member administration."""
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model


async def record_member_update(
    db: AsyncSession, actor_id: int, target_id: int, changes: dict[str, dict]
) -> None:
    """Add one 'update' row in the caller's transaction, so a failure here loses the change too.
    `changes` is {field: {"from": old, "to": new}}, changed fields only, no personal data."""
    db.add(
        model.MemberAuditEvent(
            actor_id=actor_id, action="update", target_id=target_id, detail=changes
        )
    )


async def record_member_delete(db: AsyncSession, actor_id: int, target_id: int, role: str) -> None:
    """Add one 'delete' row in the caller's transaction. The detail is the target's role only:
    the row outlives the member and has no foreign key to it."""
    db.add(
        model.MemberAuditEvent(
            actor_id=actor_id, action="delete", target_id=target_id, detail={"role": role}
        )
    )


async def record_invite_create(
    db: AsyncSession, actor_id: int, invite_id: int, role: str, expires_at
) -> None:
    """Add one 'invite_create' row in the caller's transaction. Detail is role and expiry only:
    never the code, its hash or the invited email."""
    db.add(
        model.MemberAuditEvent(
            actor_id=actor_id,
            action="invite_create",
            target_id=invite_id,
            detail={"role": role, "expires_at": expires_at.isoformat()},
        )
    )


async def record_invite_revoke(db: AsyncSession, actor_id: int, invite_id: int, role: str) -> None:
    """Add one 'invite_revoke' row in the caller's transaction. Detail is the role only:
    never the code, its hash or the invited email."""
    db.add(
        model.MemberAuditEvent(
            actor_id=actor_id,
            action="invite_revoke",
            target_id=invite_id,
            detail={"role": role},
        )
    )
