from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.item as item_crud
import app.model.model as model
import app.schema.item as item_schema
from app.db.db import get_db

router = APIRouter()


# Unfiltered, deliberately: Item.published defaults to false server-side
# (model.py:95), so a published-only filter would hide every document the CMS
# has just created from the CMS that created it.
@router.get("/items", response_model=list[item_schema.ItemResponse])
async def get_items(db: AsyncSession = Depends(get_db)):
    return await item_crud.get_items(db)


@router.post(
    "/items",
    response_model=item_schema.ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    body: item_schema.ItemCreate,
    db: AsyncSession = Depends(get_db),
):
    # Both checked here rather than left to the foreign keys: an FK violation is
    # an IntegrityError, which reaches the client as an opaque HTTP 500.
    if not await db.get(model.SubCategory, body.sub_category_id):
        raise HTTPException(status_code=404, detail="Sub-category not found")
    if not await db.get(model.Crop, body.crop_id):
        raise HTTPException(status_code=404, detail="Crop not found")
    return await item_crud.create_item(db, body)
