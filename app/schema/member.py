"""Member request/response shapes. MemberResponse and MemberAdminView have no
password_hash field, so the hash cannot leak through response_model."""
from datetime import date, datetime

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

import app.model.model as model

# ceiling for an admin-set tokens_budget_daily: an assumption, change it here.
MAX_TOKENS_BUDGET_DAILY = 10_000_000
# longest an invite may stay valid, in days.
MAX_INVITE_DAYS = 30
# one rule for the route and scripts/make_invite.py. Surrounding whitespace is allowed (stripped
# on save); control characters are refused (NUL breaks PostgreSQL; \x1c-\x1f are stripped by
# str.strip but not by the route's Rust regex).
INVITE_EMAIL_PATTERN = r"^\s*[^@\s\x00-\x1f]+@[^@\s\x00-\x1f]+\s*$"
INVITE_EMAIL_MIN = 3
INVITE_EMAIL_MAX = 255


_INVITE_EMAIL = TypeAdapter(
    Annotated[str, Field(min_length=INVITE_EMAIL_MIN, max_length=INVITE_EMAIL_MAX, pattern=INVITE_EMAIL_PATTERN)]
)


def check_invite_email(email: str) -> None:
    """Raise ValueError unless `email` passes what the route accepts (same engine)."""
    try:
        _INVITE_EMAIL.validate_python(email)
    except ValidationError as error:
        raise ValueError(f"email must look like a@b, {INVITE_EMAIL_MIN}-{INVITE_EMAIL_MAX} characters, got {email!r}") from error


class MemberCreate(BaseModel):
    # The pattern also refuses NUL, which PostgreSQL rejects in text (a 500 otherwise).
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s\x00]+@[^@\s\x00]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)
    # required: signup admits only an unused, unexpired invite; the role comes from the invite.
    invite_code: str = Field(min_length=20)

    @field_validator("display_name")
    @classmethod
    def _no_nul(cls, value):
        # PostgreSQL rejects NUL in text. (Lone surrogates: pydantic refuses them already.)
        if value is not None and "\x00" in value:
            raise ValueError("must not contain NUL")
        return value


class InviteCreate(BaseModel):
    """What an admin sends to create an invite. The role is limited to what
    INVITE_ROLES allows (never admin); the email, if given, binds the invite to
    that address (surrounding whitespace is allowed and stripped on save). The pattern
    also refuses NUL, which PostgreSQL rejects in text."""

    model_config = ConfigDict(extra="forbid")

    role: Literal[model.INVITE_ROLES] = "member"
    expires_in_days: int = Field(default=7, strict=True, ge=1, le=MAX_INVITE_DAYS)
    email: str | None = Field(
        default=None,
        min_length=INVITE_EMAIL_MIN,
        max_length=INVITE_EMAIL_MAX,
        pattern=INVITE_EMAIL_PATTERN,
    )


class InviteResponse(BaseModel):
    """The only time the plaintext code is ever returned."""

    id: int
    code: str
    role: str
    email: str | None
    expires_at: datetime


class InviteAdminView(BaseModel):
    """An open invite as an admin lists it. No code (it is shown once, at creation)
    and no code_hash: neither field exists here, so neither can leak."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    email: str | None
    created_at: datetime
    expires_at: datetime


class MemberLogin(BaseModel):
    # no min_length: an empty email stays a 401. A length or pattern also makes pydantic refuse lone
    # surrogates; the 422 then needs the handler in app/main.py.
    email: str = Field(max_length=255, pattern=r"^[^\x00]*$")
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str | None
    tokens_used_today: int
    tokens_budget_daily: int
    budget_window_start: date
    role: str


class MemberAdminView(BaseModel):
    """What an admin sees of a member: everything but the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str | None
    role: str
    is_active: bool
    tokens_used_today: int
    tokens_budget_daily: int
    budget_window_start: date


class MemberAdminUpdate(BaseModel):
    """What an admin may change on a member. extra="forbid": any other field
    (tokens_used_today, password_hash, ...) is a 422, not silently ignored. Only
    sent fields change; an explicit null is a 422 (the defaults are never validated)."""

    model_config = ConfigDict(extra="forbid")

    role: Literal[model.MEMBER_ROLES] = None
    # strict: a JSON integer only (true, "5", 5.0 and 1e3 are 422).
    tokens_budget_daily: int = Field(default=None, strict=True, ge=0, le=MAX_TOKENS_BUDGET_DAILY)
    # strict: a JSON boolean only ("true", 1 and null are 422).
    is_active: bool = Field(default=None, strict=True)

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be sent")
        return self
