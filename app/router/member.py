from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.audit as audit_crud
import app.crud.member as member_crud
import app.model.model as model
import app.schema.member as member_schema
from app.auth.auth import DUMMY_HASH, create_access_token, hash_password, verify_password
from app.auth.dependency import get_current_member, require_admin
from app.db.db import get_db

router = APIRouter(prefix="/members")

# One message for an unknown email and a wrong password: distinct ones would
# tell an attacker which emails are registered.
_BAD_LOGIN = "Incorrect email or password"

# members.id is a 32-bit integer: a larger id in the path would reach the database as an overflow (500).
MAX_MEMBER_ID = 2**31 - 1


# Admin-only. Declared before the "/{member_id}" routes at the end of this module,
# whose path parameter would otherwise shadow the static routes.
@router.get(
    "",
    response_model=list[member_schema.MemberAdminView],
    dependencies=[Depends(require_admin)],
)
async def list_members(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    role: Literal[model.MEMBER_ROLES] | None = None,
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await member_crud.list_members(db, limit, offset, role, is_active)


@router.post("/signup", response_model=member_schema.MemberResponse, status_code=201)
async def signup(payload: member_schema.MemberCreate, db: AsyncSession = Depends(get_db)):
    email = payload.email.strip().lower()
    if await member_crud.get_member_by_email(db, email) is not None:
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed = await hash_password(payload.password)
    try:
        return await member_crud.create_member(db, email, hashed, payload.display_name)
    except IntegrityError:
        # Two signups for one email racing past the check above; the UNIQUE
        # constraint is the real guard.
        await db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")


@router.post("/login", response_model=member_schema.TokenResponse)
async def login(payload: member_schema.MemberLogin, db: AsyncSession = Depends(get_db)):
    member = await member_crud.get_member_by_email(db, payload.email.strip().lower())
    if member is None:
        # Equalise timing with the wrong-password path (see DUMMY_HASH); the
        # result is discarded.
        await verify_password(payload.password, DUMMY_HASH)
        raise HTTPException(status_code=401, detail=_BAD_LOGIN)
    if not await verify_password(payload.password, member.password_hash):
        raise HTTPException(status_code=401, detail=_BAD_LOGIN)
    if not member.is_active:
        # Checked only after the password verified, so an inactive member costs
        # the same argon2 work and gets the very same 401 (body and headers) as
        # a wrong password: nothing tells a caller the account is deactivated.
        raise HTTPException(status_code=401, detail=_BAD_LOGIN)
    return member_schema.TokenResponse(access_token=create_access_token(member.id))


@router.get("/me", response_model=member_schema.MemberResponse)
async def me(current_member: model.Member = Depends(get_current_member)):
    return current_member


def last_admin_refusal(
    target_id: int, admin_ids: list[int], changes: dict, deleting: bool = False
) -> str | None:
    """The last-admin rule as a decision on the locked rows: the refusal message if
    `changes` would remove the role of, or deactivate, the only active admin (the
    target), or if `deleting` would delete them, else None. `admin_ids` are the
    active admins the lock statement returned. With the actor check in the handlers, a request
    reaches this with the target as the only active admin only if the actor IS the
    target (the self rule answers first), so it is defence in depth for the
    invariant, and tested on its own. PATCH and DELETE share this one decision."""
    if admin_ids != [target_id]:
        return None
    if deleting:
        return "The last active admin cannot be deleted"
    if "role" in changes:
        return "The last active admin cannot lose the admin role"
    if "is_active" in changes and changes["is_active"]["to"] is False:
        return "The last active admin cannot be deactivated"
    return None


# Declared last: a path parameter would otherwise shadow the static routes above.
@router.patch("/{member_id}", response_model=member_schema.MemberAdminView)
async def update_member(
    member_id: int = Path(ge=1, le=MAX_MEMBER_ID),
    payload: member_schema.MemberAdminUpdate = Body(),
    actor: model.Member = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Order of checks: auth (the dependencies) -> 422 (the body) -> lock -> actor
    # still an active admin -> 404 -> self rules (role change or deactivation of
    # oneself) -> last-admin rule (role removal or deactivation of the sole active
    # admin) -> no-op 200 -> flush -> audit -> one commit. A refusal writes no audit row.
    # One locking statement for the target and every active admin (see the crud
    # docstring for why not two), so every check below reads state nobody else
    # can change until we commit. Any other writer that locks several member rows
    # must do the same: lock in ascending id, or reuse that function.
    target, admin_ids = await member_crud.lock_member_and_active_admins(db, member_id)
    # The guard checked the actor before the lock; a concurrent request may have
    # deactivated or demoted them since. admin_ids is read under the lock, so an
    # actor missing from it is no longer an active admin: refuse, whatever the
    # field (a just-demoted admin must not promote anyone). 403, not 401: the
    # token was valid when the request began, and the actor's NEXT request is the
    # guard's 401 (deactivated) or 403 (demoted). Decided before the 404 so a
    # stale actor learns nothing about which ids exist.
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
        return target  # nothing changed, so nothing to audit
    for field, change in changes.items():
        setattr(target, field, change["to"])
    await db.flush()
    # Same transaction as the change: if this raises, the change is rolled back.
    await audit_crud.record_member_update(db, actor.id, target.id, changes)
    await db.commit()
    return target


@router.delete("/{member_id}", status_code=204)
async def delete_member(
    member_id: int = Path(ge=1, le=MAX_MEMBER_ID),
    actor: model.Member = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Order of checks, as for PATCH: auth (the dependencies) -> 422 (the path) ->
    # lock -> actor still an active admin -> 404 -> self rule (an admin cannot
    # delete themselves) -> last-admin rule -> delete -> audit -> one commit. A
    # refusal deletes nothing and writes no audit row.
    # The SAME ordered locking statement as PATCH, not a second locking path: any
    # writer that locks several member rows must lock in ascending id or reuse
    # that function, or two requests can deadlock (see the crud docstring).
    target, admin_ids = await member_crud.lock_member_and_active_admins(db, member_id)
    # Stale actor (deactivated or demoted since the guard): 403 before the 404, so
    # it learns nothing about which ids exist. See update_member.
    if actor.id not in admin_ids:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.id == actor.id:
        raise HTTPException(status_code=409, detail="An admin cannot delete themselves")
    refusal = last_admin_refusal(target.id, admin_ids, {}, deleting=True)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)
    role = target.role  # read before the row goes; the audit keeps only this
    # A hard delete of the members row and nothing else: no other table references
    # members yet, so nothing cascades. Brief G (chambers, grows) decides ON DELETE
    # CASCADE versus RESTRICT when it adds the first reference.
    await db.delete(target)
    await db.flush()
    # Same transaction as the delete: if this raises, the member is not deleted.
    await audit_crud.record_member_delete(db, actor.id, member_id, role)
    await db.commit()
