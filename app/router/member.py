from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.member as member_crud
import app.model.model as model
import app.schema.member as member_schema
from app.auth.auth import DUMMY_HASH, create_access_token, hash_password, verify_password
from app.auth.dependency import get_current_member
from app.db.db import get_db

router = APIRouter(prefix="/members")

# One message for an unknown email and a wrong password: distinct ones would
# tell an attacker which emails are registered.
_BAD_LOGIN = "Incorrect email or password"


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
    return member_schema.TokenResponse(access_token=create_access_token(member.id))


@router.get("/me", response_model=member_schema.MemberResponse)
async def me(current_member: model.Member = Depends(get_current_member)):
    return current_member
