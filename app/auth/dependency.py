"""get_current_member: the dependency every members-only route takes."""
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
