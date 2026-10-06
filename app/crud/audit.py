"""Audit trail for member administration."""
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model


async def record_member_update(
    db: AsyncSession, actor_id: int, target_id: int, changes: dict[str, dict]
) -> None:
    """Add one 'update' row; the caller's commit writes it with the change it
    describes, in one transaction, so a failure here loses the change too.
    `changes` is {field: {"from": old, "to": new}}, changed fields only: no email,
    no other personal data."""
    db.add(
        model.MemberAuditEvent(
            actor_id=actor_id, action="update", target_id=target_id, detail=changes
        )
    )


async def record_member_delete(db: AsyncSession, actor_id: int, target_id: int, role: str) -> None:
    """Add one 'delete' row; the caller's commit writes it with the deletion, in one
    transaction. The detail is the target's role and nothing else (no email, no
    display name): the row outlives the member and has no foreign key to it."""
    db.add(
        model.MemberAuditEvent(
            actor_id=actor_id, action="delete", target_id=target_id, detail={"role": role}
        )
    )
