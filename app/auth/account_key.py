"""The login-throttle key of an email. Imports nothing but normalise_email: no settings, no DB, so
an operator script can use it even when the throttle's own settings are broken."""
import hashlib

from app.auth.invite import normalise_email


def throttle_key(email: str) -> str:
    return hashlib.sha256(normalise_email(email).encode()).hexdigest()
