from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.item as item_crud
import app.model.model as model
import app.schema.item as item_schema
from app.auth.dependency import require_editor
from app.db.db import get_db

router = APIRouter()


# unfiltered on purpose: Item.published defaults to false (see app/crud/item.py).
@router.get("/items", response_model=list[item_schema.ItemResponse])
async def get_items(db: AsyncSession = Depends(get_db)):
    return await item_crud.get_items(db)


@router.post(
    "/items",
    dependencies=[Depends(require_editor)],
    response_model=item_schema.ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    body: item_schema.ItemCreate,
    db: AsyncSession = Depends(get_db),
):
    # checked here: an FK violation would reach the client as an opaque 500
    # (as in create_sub_category).
    if not await db.get(model.SubCategory, body.sub_category_id):
        raise HTTPException(status_code=404, detail="Sub-category not found")
    if not await db.get(model.Crop, body.crop_id):
        raise HTTPException(status_code=404, detail="Crop not found")
    return await item_crud.create_item(db, body)


@router.delete(
    "/items/{item_id}",
    dependencies=[Depends(require_editor)],
    response_model=item_schema.ItemResponse,
)
async def delete_item(
    # the lower bound is MIN_INT4, not 1: 0 and negatives are a plain 404, only ids Postgres
    # cannot compare (outside int4) are 422.
    item_id: int = Path(ge=item_schema.MIN_INT4, le=item_schema.MAX_INT4),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a document and its chunks; 200 returns the deleted row, since there is no
    backup or audit record to recover it from."""
    item = await item_crud.delete_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
