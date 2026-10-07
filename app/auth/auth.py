"""Password hashing and JWT signing. No database, no FastAPI.

Argon2 is slow, so hash/verify run in the threadpool (inline they would stall every request).
No refresh tokens: a token lives ACCESS_TOKEN_EXPIRE_MINUTES.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

_password_hash = PasswordHash.recommended()  # Argon2id

# a real argon2 hash of a throwaway string: login verifies against it for an unknown email,
# so that path costs as much as a wrong password and timing does not reveal registered emails.
DUMMY_HASH = _password_hash.hash(secrets.token_hex(16))


def get_secret_key() -> str:
    """The signing key. No default, so a missing one fails loudly, but read at call time (unlike
    DB_PASSWORD) so the offline test suite can import the app with no SECRET_KEY set."""
    key = os.environ["SECRET_KEY"]
    if not key:  # compose's bare ${SECRET_KEY} passes "" when .env lacks it
        raise KeyError("SECRET_KEY is set but empty")
    return key


async def hash_password(plain: str) -> str:
    return await run_in_threadpool(_password_hash.hash, plain)


async def verify_password(plain: str, hashed: str) -> bool:
    return await run_in_threadpool(_password_hash.verify, plain, hashed)


def create_access_token(member_id: int, expires_minutes: int | None = None) -> str:
    minutes = ACCESS_TOKEN_EXPIRE_MINUTES if expires_minutes is None else expires_minutes
    now = datetime.now(timezone.utc)
    payload = {"sub": str(member_id), "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """The member id inside a valid, unexpired token; None for anything else."""
    try:
        payload = jwt.decode(
            token, get_secret_key(), algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        return None
