"""Data access for crops.

Read-only by design: crops are seeded to match the advisor's
data/ecocrop/<slug>.json rather than authored here, and deleting one cascades to
every document about it (model.py:88). Settled in PR #2.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model


async def get_crops(db: AsyncSession) -> list[model.Crop]:
    result = await db.execute(select(model.Crop).order_by(model.Crop.slug))
    return list(result.scalars().all())
