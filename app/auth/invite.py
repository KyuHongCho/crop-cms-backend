"""Invite codes: generate, hash, and normalise an invite email. No database.

32 random bytes (256 bits), shown once; only the SHA-256 is stored. A fast hash suffices: with
that much entropy there is nothing to brute-force, unlike a password.
"""
import hashlib
import secrets


def generate_code() -> str:
    return secrets.token_urlsafe(32)


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def normalise_email(email: str) -> str:
    # same expression signup uses to look members up, so a bound invite matches in any letter
    # case; the route and scripts/make_invite.py both call this.
    return email.strip().lower()
