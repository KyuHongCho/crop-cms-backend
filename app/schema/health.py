"""Response shape for the probe routes. Status only: no version, host or database detail."""
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
