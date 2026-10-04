"""Member request/response shapes. MemberResponse has no password_hash field,
so the hash cannot leak through response_model."""
from datetime import date

from pydantic import BaseModel, ConfigDict, Field


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
