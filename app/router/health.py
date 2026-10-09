"""Open probe routes, for a container healthcheck or a load balancer."""
import asyncio
import logging

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import app.schema.health as health_schema
from app.db.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# Without a bound, a black-holed or hung database blocks in connect: no connect_timeout
# is set, and the pool's 30 s covers only waiting for a free slot.
READY_TIMEOUT_SECONDS = 3


@router.get("/health", response_model=health_schema.HealthResponse)
def get_health(response: Response):
    # no-store: a proxy must never serve a stale "ok".
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}


@router.get(
    "/health/ready",
    response_model=health_schema.HealthResponse,
    responses={503: {"model": health_schema.HealthResponse}},
)
async def get_ready(response: Response, db: AsyncSession = Depends(get_db)):
    try:
        async with asyncio.timeout(READY_TIMEOUT_SECONDS):
            await db.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        # Class name only: str(exc) carries the database host and port.
        logger.warning("readiness check failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content=health_schema.HealthResponse(status="unavailable").model_dump(),
            headers={"Cache-Control": "no-store"},
        )
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}
