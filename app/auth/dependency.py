"""get_current_member: the dependency every members-only route takes, and the
role guards built on it (require_editor, require_admin)."""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.member as member_crud
import app.model.model as model
from app.auth.auth import decode_access_token
from app.db.db import get_db

# auto_error=False so a missing header is the same 401 as a bad token, rather
# than whatever status the framework picks for it.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_member(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> model.Member:
    unauthorised = HTTPException(
        status_code=401,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorised
    member_id = decode_access_token(credentials.credentials)
    if member_id is None:
        raise unauthorised
    member = await member_crud.get_member(db, member_id)
    if member is None:
        raise unauthorised
    return member


def require_roles(*allowed: str):
    """A dependency that lets only members holding one of `allowed` roles through.

    401 when there is no valid token (from get_current_member), 403 when the
    member is known but their role is not enough. The role is read from the
    member row on every request, never from the token, so a demotion takes
    effect on the next request instead of when the token expires.
    """
    unknown = set(allowed) - set(model.MEMBER_ROLES)
    if unknown:
        raise ValueError(f"unknown role(s): {sorted(unknown)}")

    async def guard(member: model.Member = Depends(get_current_member)) -> model.Member:
        if member.role not in allowed:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return member

    # Read by the route-guard test to see which roles each route admits.
    guard.allowed_roles = tuple(allowed)
    return guard


# Content (add and delete) is for editors and admins.
require_editor = require_roles("editor", "admin")
# Defined ahead of the first admin-only route (member management).
require_admin = require_roles("admin")
