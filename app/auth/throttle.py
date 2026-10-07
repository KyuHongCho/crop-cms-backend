"""Login throttle: fixed-window attempt counter per account, in the login_throttle table.
Each call commits on its own connection, so a later rollback, 401 or cancellation cannot undo it."""
import os
import uuid
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def _read_setting(name: str, default: int, maximum: int | None = None) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer >= 1, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}, got {value}")
    return value


# the window cap keeps make_interval / now() - interval from overflowing (about 1e12 s) into a 500 on every login.
# read at import; the statements take them at call time, so tests can monkeypatch these names.
LOGIN_MAX_FAILURES = _read_setting("LOGIN_MAX_FAILURES", 10)
LOGIN_WINDOW_SECONDS = _read_setting("LOGIN_WINDOW_SECONDS", 900, maximum=315360000)

_EXPIRED = "login_throttle.window_started_at <= now() - make_interval(secs => CAST(:window AS double precision))"

# One statement, so concurrent attempts cannot all pass a count-then-insert check: under READ
# COMMITTED the DO UPDATE ... WHERE is re-checked against the freshly locked row. That is why
# the window is fixed. No row returned means blocked. The CTE prunes up to 20 other expired rows.
RESERVE_SQL = text(f"""
WITH pruned AS (
    DELETE FROM login_throttle WHERE email_key IN (
        SELECT email_key FROM login_throttle
        WHERE window_started_at <= now() - make_interval(secs => CAST(:window AS double precision))
          AND email_key <> :key
        ORDER BY window_started_at LIMIT 20 FOR UPDATE SKIP LOCKED
    )
)
INSERT INTO login_throttle (email_key, attempts) VALUES (:key, 1)
ON CONFLICT (email_key) DO UPDATE SET
    attempts = CASE WHEN {_EXPIRED} THEN 1 ELSE login_throttle.attempts + 1 END,
    window_started_at = CASE WHEN {_EXPIRED} THEN now() ELSE login_throttle.window_started_at END,
    window_id = CASE WHEN {_EXPIRED} THEN gen_random_uuid() ELSE login_throttle.window_id END
WHERE {_EXPIRED} OR login_throttle.attempts < :max
RETURNING window_id, attempts
""")

RELEASE_SQL = text(
    "UPDATE login_throttle SET attempts = attempts - 1 "
    "WHERE email_key = :key AND window_id = :window_id AND attempts > 0"
)
CLEAR_SQL = text("DELETE FROM login_throttle WHERE email_key = :key")
_RETRY_SQL = text(
    "SELECT EXTRACT(EPOCH FROM window_started_at + make_interval(secs => CAST(:window AS double precision)) - now()) "
    "FROM login_throttle WHERE email_key = :key"
)


class Reservation(NamedTuple):
    window_id: uuid.UUID
    attempts: int


async def reserve(bind: AsyncEngine, key: str) -> Reservation | None:
    """Count one attempt. None means the account is blocked and nothing was written."""
    async with bind.begin() as conn:
        result = await conn.execute(
            RESERVE_SQL, {"key": key, "window": LOGIN_WINDOW_SECONDS, "max": LOGIN_MAX_FAILURES}
        )
        row = result.first()
    return None if row is None else Reservation(row.window_id, row.attempts)


async def release(bind: AsyncEngine, key: str, window_id: uuid.UUID) -> None:
    """Give one attempt back, only within the window it was reserved in."""
    async with bind.begin() as conn:
        await conn.execute(RELEASE_SQL, {"key": key, "window_id": window_id})


async def clear(bind: AsyncEngine, key: str) -> None:
    async with bind.begin() as conn:
        await conn.execute(CLEAR_SQL, {"key": key})


async def retry_after(bind: AsyncEngine, key: str) -> int:
    """Whole seconds until the window ends, clamped to 1..LOGIN_WINDOW_SECONDS."""
    async with bind.begin() as conn:
        seconds = (await conn.execute(_RETRY_SQL, {"key": key, "window": LOGIN_WINDOW_SECONDS})).scalar()
    if seconds is None:
        return LOGIN_WINDOW_SECONDS
    return max(1, min(LOGIN_WINDOW_SECONDS, int(-(-float(seconds) // 1))))
