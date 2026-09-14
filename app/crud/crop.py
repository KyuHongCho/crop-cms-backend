"""Data access for crops.

Read-only by design -- see app/schema/crop.py's module docstring for why
(crops mirror the advisor's ECOCROP data) and CropResponse for the field
mapping.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model


async def get_crops(db: AsyncSession) -> list[model.Crop]:
    result = await db.execute(select(model.Crop).order_by(model.Crop.slug))
    return list(result.scalars().all())
