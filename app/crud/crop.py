"""Data access for crops.

Read-only by design: crops are seeded to match the advisor's
data/ecocrop/<slug>.json rather than authored here. Settled in PR #2.
Item.crop_id is ON DELETE RESTRICT, so a crop that still has documents cannot
be deleted.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model


async def get_crops(db: AsyncSession) -> list[model.Crop]:
    result = await db.execute(select(model.Crop).order_by(model.Crop.slug))
    return list(result.scalars().all())
