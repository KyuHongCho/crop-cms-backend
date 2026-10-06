"""Member request/response shapes. MemberResponse and MemberAdminView have no
password_hash field, so the hash cannot leak through response_model."""
from datetime import date

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import app.model.model as model

# Ceiling for tokens_budget_daily set by an admin: an assumption, change it here.
MAX_TOKENS_BUDGET_DAILY = 10_000_000


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)


class MemberLogin(BaseModel):
    email: str
    password: str


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
    # strict: a JSON integer only (true, "5", 5.0 and 1e3 are 422, not coerced).
    tokens_budget_daily: int = Field(default=None, strict=True, ge=0, le=MAX_TOKENS_BUDGET_DAILY)
    # strict: a JSON boolean only ("true", 1 and null are 422).
    is_active: bool = Field(default=None, strict=True)

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be sent")
        return self
