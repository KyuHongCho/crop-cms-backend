"""Data access for narrative documents."""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model
import app.schema.item as item_schema


async def get_items(
    db: AsyncSession, include_unpublished: bool = False, limit: int = 500, offset: int = 0
) -> list[model.Item]:
    """A page of published documents, or of all when `include_unpublished` (the caller gates it).

    Item.published defaults to false, so a new document is a draft until published."""
    query = select(model.Item).order_by(model.Item.id)
    if not include_unpublished:
        query = query.where(model.Item.published.is_(True))
    result = await db.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


async def create_item(db: AsyncSession, body: item_schema.ItemCreate) -> model.Item:
    item = model.Item(**body.model_dump())
    db.add(item)
    await db.commit()
    # no refresh: ItemResponse reads only columns, which expire_on_commit=False leaves populated.
    return item


async def delete_item(db: AsyncSession, item_id: int) -> model.Item | None:
    """Hard-delete one document and return it, or None if there is no such id.

    The row is returned because there is no backup, so the caller must keep it.
    """
    result = await db.execute(
        delete(model.Item).where(model.Item.id == item_id).returning(model.Item)
    )
    item = result.scalar_one_or_none()
    await db.commit()
    return item
