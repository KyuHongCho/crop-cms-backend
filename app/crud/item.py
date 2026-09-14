"""Data access for narrative documents."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model
import app.schema.item as item_schema


async def get_items(db: AsyncSession) -> list[model.Item]:
    """Every document, unfiltered.

    Deliberately not published-only: Item.published defaults to false
    server-side (see model.py), so a published-only filter would hide every
    freshly created document from the CMS that just created it. The
    advisor's published-only retrieval lives on a separate endpoint
    instead -- crop plus topic, the whole topic set, never truncated.
    """
    result = await db.execute(select(model.Item).order_by(model.Item.id))
    return list(result.scalars().all())


async def create_item(db: AsyncSession, body: item_schema.ItemCreate) -> model.Item:
    item = model.Item(**body.model_dump())
    db.add(item)
    await db.commit()
    # No refresh needed: ItemResponse reads only columns, and
    # expire_on_commit=False in db.py leaves them populated after commit.
    return item
