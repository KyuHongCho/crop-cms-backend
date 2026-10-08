import logging
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.auth.throttle as throttle
import app.crud.audit as audit_crud
import app.crud.invite as invite_crud
import app.crud.member as member_crud
import app.model.model as model
import app.schema.member as member_schema
from app.auth.account_key import throttle_key
from app.auth.auth import DUMMY_HASH, create_access_token, hash_password, verify_password
from app.auth.dependency import get_current_member, require_admin
from app.auth.invite import normalise_email
from app.db.db import get_db
from app.schema.item import MAX_OFFSET

router = APIRouter(prefix="/members")
logger = logging.getLogger(__name__)

# one message for unknown email and wrong password: distinct ones reveal which emails exist.
_BAD_LOGIN = "Incorrect email or password"

# path ids above these would overflow the column (500): members.id and member_invites.id are 32-bit.
MAX_MEMBER_ID = 2**31 - 1
MAX_INVITE_ID = 2**31 - 1


# declared before "/{member_id}", whose path parameter would shadow static routes.
@router.get(
    "",
    response_model=list[member_schema.MemberAdminView],
    dependencies=[Depends(require_admin)],
)
async def list_members(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    role: Literal[model.MEMBER_ROLES] | None = None,
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await member_crud.list_members(db, limit, offset, role, is_active)


# declared before "/{member_id}", which would read the literal "invites" segment as an id.
@router.get(
    "/invites",
    response_model=list[member_schema.InviteAdminView],
    dependencies=[Depends(require_admin)],
)
async def list_invites(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    db: AsyncSession = Depends(get_db),
):
    return await invite_crud.list_open_invites(db, limit, offset)


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: int = Path(ge=1, le=MAX_INVITE_ID),
    actor: model.Member = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # 403 before the 404 so a stale actor learns nothing about which ids exist. The actor
    # row is read FOR SHARE: a concurrent demotion waits for our commit instead of racing.
    # Hard delete; a used invite stays (409) as the record that the code was claimed.
    if not await member_crud.actor_is_active_admin(db, actor.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    invite = await invite_crud.lock_invite(db, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.used_at is not None:
        raise HTTPException(status_code=409, detail="A used invite cannot be revoked")
    role = invite.role  # the audit keeps only this, and the row is about to go
    await db.delete(invite)
    await db.flush()
    # same transaction as the delete: if this raises, the invite is not deleted.
    await audit_crud.record_invite_revoke(db, actor.id, invite_id, role)
    await db.commit()


@router.post("/invites", response_model=member_schema.InviteResponse, status_code=201)
async def create_invite(
    payload: member_schema.InviteCreate,
    actor: model.Member = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # as in revoke_invite: a just-demoted admin must not mint an invite (an editor invite
    # is a promotion); FOR SHARE makes a concurrent demotion wait for our commit.
    if not await member_crud.actor_is_active_admin(db, actor.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    invite, code = await invite_crud.create_invite(
        db, payload.role, payload.expires_in_days, payload.email
    )
    # same transaction as the invite: if this raises, no invite is stored.
    await audit_crud.record_invite_create(db, actor.id, invite.id, invite.role, invite.expires_at)
    await db.commit()
    return member_schema.InviteResponse(
        id=invite.id,
        code=code,
        role=invite.role,
        email=invite.email,
        expires_at=invite.expires_at,
    )


@router.post("/signup", response_model=member_schema.MemberResponse, status_code=201)
async def signup(payload: member_schema.MemberCreate, db: AsyncSession = Depends(get_db)):
    email = normalise_email(payload.email)
    # first: a bad code costs one cheap UPDATE and gets one answer (unknown/used/expired/wrong
    # email). A match holds the invite lock across the ~25 ms hashing below, so pool exhaustion
    # is the risk: test_twenty_concurrent_signups_do_not_exhaust_the_pool (it stubs 0.2 s).
    role = await invite_crud.claim_invite(db, payload.invite_code, email)
    if role is None:
        raise HTTPException(status_code=400, detail="Invalid or expired invite")
    if await member_crud.get_member_by_email(db, email) is not None:
        await db.rollback()  # explicit un-claim of the invite
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed = await hash_password(payload.password)
    try:
        # one commit for the claim and the member: both stand or neither does.
        return await member_crud.create_member(db, email, hashed, payload.display_name, role)
    except IntegrityError:
        # two signups racing past the check above; UNIQUE is the real guard, and the
        # rollback un-claims the invite.
        await db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")


@router.post("/login", response_model=member_schema.TokenResponse)
async def login(payload: member_schema.MemberLogin, db: AsyncSession = Depends(get_db)):
    key = throttle_key(payload.email)
    # first DB statement, on its own connection. Fail-closed: if it raises, nothing else runs.
    reservation = await throttle.reserve(db.bind, key)
    if reservation is None:
        wait = await throttle.retry_after(db.bind, key)
        raise HTTPException(
            status_code=429, detail="Too many failed attempts", headers={"Retry-After": str(wait)}
        )
    try:
        member = await member_crud.get_member_by_email(db, normalise_email(payload.email))
    except Exception:
        # no password was evaluated, so this attempt is given back. Never from a finally or on
        # BaseException: a cancelled request's argon2 thread keeps running, and that work must count.
        try:
            await throttle.release(db.bind, key, reservation.window_id)
        except Exception:
            logger.exception("login throttle: release failed, the attempt stays counted")
        raise
    member_id = member.id if member else None
    password_hash = member.password_hash if member else DUMMY_HASH  # equalise timing, see DUMMY_HASH
    is_active = member.is_active if member else False
    # end the lookup transaction so the request holds no pooled connection while argon2 runs.
    await db.rollback()
    verified = await verify_password(payload.password, password_hash)
    # one 401 for unknown email, wrong password and an inactive member (whose attempt stays counted).
    if member_id is None or not verified or not is_active:
        raise HTTPException(status_code=401, detail=_BAD_LOGIN)
    await throttle.clear(db.bind, key)
    return member_schema.TokenResponse(access_token=create_access_token(member_id))


@router.get("/me", response_model=member_schema.MemberResponse)
async def me(current_member: model.Member = Depends(get_current_member)):
    return current_member


def last_admin_refusal(
    target_id: int, admin_ids: list[int], changes: dict, deleting: bool = False
) -> str | None:
    """The last-admin rule: the refusal message if `changes` would demote or deactivate the
    only active admin (the target), or `deleting` would delete them, else None.
    Defence in depth (the self rule answers first); PATCH and DELETE share this decision."""
    if admin_ids != [target_id]:
        return None
    if deleting:
        return "The last active admin cannot be deleted"
    if "role" in changes:
        return "The last active admin cannot lose the admin role"
    if "is_active" in changes and changes["is_active"]["to"] is False:
        return "The last active admin cannot be deactivated"
    return None


# declared last: a path parameter would shadow the static routes above.
@router.patch("/{member_id}", response_model=member_schema.MemberAdminView)
async def update_member(
    member_id: int = Path(ge=1, le=MAX_MEMBER_ID),
    payload: member_schema.MemberAdminUpdate = Body(),
    actor: model.Member = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # one locking statement for the target and every active admin, so every check below
    # reads state nobody can change until we commit. Any writer locking several member
    # rows must lock in ascending id or reuse that function (else deadlock).
    target, admin_ids = await member_crud.lock_member_and_active_admins(db, member_id)
    # the guard ran before the lock; the actor may have been demoted or deactivated since.
    # 403 (not 401): the token was valid when the request began. Before the 404 so a stale
    # actor learns nothing about which ids exist.
    if actor.id not in admin_ids:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    changes = {
        field: {"from": getattr(target, field), "to": value}
        for field, value in payload.model_dump(exclude_unset=True).items()
        if getattr(target, field) != value
    }
    deactivating = "is_active" in changes and changes["is_active"]["to"] is False
    if target.id == actor.id:
        if "role" in changes:
            raise HTTPException(status_code=409, detail="An admin cannot change their own role")
        if deactivating:
            raise HTTPException(status_code=409, detail="An admin cannot deactivate themselves")
    refusal = last_admin_refusal(target.id, admin_ids, changes)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)
    if not changes:
        return target
    for field, change in changes.items():
        setattr(target, field, change["to"])
    await db.flush()
    # same transaction as the change: if this raises, the change is rolled back.
    await audit_crud.record_member_update(db, actor.id, target.id, changes)
    await db.commit()
    return target


@router.delete("/{member_id}", status_code=204)
async def delete_member(
    member_id: int = Path(ge=1, le=MAX_MEMBER_ID),
    actor: model.Member = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # same ordered locking statement as PATCH: a second locking path could deadlock
    # (see the crud docstring).
    target, admin_ids = await member_crud.lock_member_and_active_admins(db, member_id)
    # stale actor: 403 before the 404 (see update_member).
    if actor.id not in admin_ids:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.id == actor.id:
        raise HTTPException(status_code=409, detail="An admin cannot delete themselves")
    refusal = last_admin_refusal(target.id, admin_ids, {}, deleting=True)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)
    role = target.role  # the audit keeps only this, and the row is about to go
    # no foreign key references members yet, so nothing cascades; whoever adds the first
    # one decides CASCADE versus RESTRICT.
    await db.delete(target)
    await db.flush()
    # same transaction as the delete: if this raises, the member is not deleted.
    await audit_crud.record_member_delete(db, actor.id, member_id, role)
    await db.commit()


@router.post("/{member_id}/unlock", status_code=204)
async def unlock_member(
    member_id: int = Path(ge=1, le=MAX_MEMBER_ID),
    actor: model.Member = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # as in create_invite: 403 before the 404, actor row read FOR SHARE.
    if not await member_crud.actor_is_active_admin(db, actor.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    member = await member_crud.get_member(db, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    # the request session, not throttle.clear's own connection: the audit row below must be able
    # to undo this delete. Idempotent; every call is audited, cleared or not.
    result = await db.execute(
        throttle.CLEAR_SQL,
        {"key": throttle_key(member.email)},
    )
    await audit_crud.record_member_unlock(db, actor.id, member.id, result.rowcount)
    await db.commit()
