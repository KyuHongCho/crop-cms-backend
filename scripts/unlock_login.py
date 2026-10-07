"""docker compose exec -T cms python -m scripts.unlock_login ADDRESS
Unlike make_invite.py this writes an `unlock` audit row (actor 0): the operator's action belongs on record.
Must not import app.auth.throttle: it validates LOGIN_* at import, and this must work when those are bad."""
import argparse
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.account_key import throttle_key
from app.auth.invite import normalise_email
from app.crud.audit import SYSTEM_ACTOR_ID, build_unlock_event
from app.db.migrate_db import engine as sync_engine

DELETE_SQL = text("DELETE FROM login_throttle WHERE email_key = :key RETURNING 1")
MEMBER_SQL = text("SELECT id FROM members WHERE email = :email")


def unlock_login(email: str, engine=sync_engine) -> int:
    """Delete the account's throttle row and add one audit row, in one transaction.
    Returns how many throttle rows were cleared (0 or 1)."""
    key = throttle_key(email)
    print(
        f"unlock_login: DB_HOST={engine.url.host} DB_NAME={engine.url.database}", file=sys.stderr
    )
    with Session(engine) as session:
        cleared = len(session.execute(DELETE_SQL, {"key": key}).all())
        member_id = session.execute(MEMBER_SQL, {"email": normalise_email(email)}).scalar_one_or_none()
        session.add(build_unlock_event(SYSTEM_ACTOR_ID, member_id, cleared))
        session.commit()
    return cleared


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear one account's login throttle.")
    parser.add_argument("email", help="the account's address; case and padding do not matter")
    args = parser.parse_args()
    try:
        throttle_key(args.email)
    except UnicodeEncodeError:
        # Invalid UTF-8 in argv arrives as a lone surrogate; refuse before touching the database.
        print("unlock_login: the address is not valid UTF-8", file=sys.stderr)
        sys.exit(2)
    # The shared engine echoes every statement; keep stdout to the result alone.
    sync_engine.echo = False
    print("unlocked" if unlock_login(args.email) else "nothing to unlock")


if __name__ == "__main__":
    main()
