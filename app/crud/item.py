"""Data access for narrative documents."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model
import app.schema.item as item_schema


async def get_items(db: AsyncSession) -> list[model.Item]:
    """Every document, unfiltered.

    Not published-only: Item.published defaults to false, so that filter would hide a new
    document from the CMS that just created it. Published retrieval is a separate endpoint.
    """
    result = await db.execute(select(model.Item).order_by(model.Item.id))
    return list(result.scalars().all())


async def create_item(db: AsyncSession, body: item_schema.ItemCreate) -> model.Item:
    item = model.Item(**body.model_dump())
    db.add(item)
    await db.commit()
    # no refresh: ItemResponse reads only columns, which expire_on_commit=False leaves populated.
    return item
