"""Prints a signup invite code, once. For the operator's first invite (there is no
admin yet to ask the API), or any time the API is not to hand.

    docker compose exec -T cms python -m scripts.make_invite
    docker compose exec -T cms python -m scripts.make_invite --role editor --days 3 --email grower@example.com

Writes straight to the database through the synchronous engine, like
`scripts/seed.py`. Only the code's SHA-256 is stored, so the printed code cannot
be shown again. Unlike the API route this writes no audit row: the audit trail
records an acting member and the operator is not one.
"""
import argparse

from sqlalchemy.orm import Session

import app.model.model as model
from app.crud.invite import build_invite
from app.db.migrate_db import engine as sync_engine
from app.schema.member import MAX_INVITE_DAYS, check_invite_email


def make_invite(role: str = "member", days: int = 7, email: str | None = None) -> str:
    """Store one invite and return its plaintext code."""
    if role not in model.INVITE_ROLES:
        raise ValueError(f"role must be one of {model.INVITE_ROLES}, got {role!r}")
    if not 1 <= days <= MAX_INVITE_DAYS:
        raise ValueError(f"days must be 1..{MAX_INVITE_DAYS}, got {days}")
    if email is not None:
        check_invite_email(email)
    invite, code = build_invite(role, days, email)
    with Session(sync_engine) as session:
        session.add(invite)
        session.commit()
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--role", choices=model.INVITE_ROLES, default="member")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--email", default=None, help="bind the invite to this address")
    args = parser.parse_args()
    # The shared engine echoes every statement; keep stdout to the code alone, so
    # `CODE=$(python -m scripts.make_invite)` works.
    sync_engine.echo = False
    print(make_invite(args.role, args.days, args.email))


if __name__ == "__main__":
    main()
