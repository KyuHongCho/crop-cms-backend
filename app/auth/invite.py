"""Invite codes: generate, hash, and normalise an invite email. No database.

The code is 32 random bytes, URL-safe base64 (43 characters), shown once at
creation. Only its SHA-256 hex digest is stored, so a database read does not
yield a usable code; a fast hash is enough because the code has 256 bits of
entropy (there is nothing to brute-force, unlike a password).
"""
import hashlib
import secrets


def generate_code() -> str:
    return secrets.token_urlsafe(32)


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def normalise_email(email: str) -> str:
    # The same expression signup applies to the email it looks members up by
    # (app/router/member.py), so an invite bound to an address matches it in any
    # letter case. The route and scripts/make_invite.py both call this.
    return email.strip().lower()
