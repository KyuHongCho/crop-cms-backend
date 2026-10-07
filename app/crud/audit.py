"""Audit trail for member administration."""
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model

# 0 = operator or script, never a member id (members.id starts at 1). A reader that joins
# actor_id to members must treat it as "not a member".
SYSTEM_ACTOR_ID = 0


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


def build_unlock_event(actor_id: int, target_id: int | None, cleared: int) -> model.MemberAuditEvent:
    """The 'unlock' row. Detail is the cleared count only (0 or 1): never the email or the throttle
    key, which is a digest of it."""
    return model.MemberAuditEvent(
        actor_id=actor_id, action="unlock", target_id=target_id, detail={"cleared": cleared}
    )


async def record_member_unlock(
    db: AsyncSession, actor_id: int, target_id: int | None, cleared: int
) -> None:
    """Add one 'unlock' row in the caller's transaction, so a failure here loses the delete too."""
    db.add(build_unlock_event(actor_id, target_id, cleared))
