from fastapi import APIRouter, Depends, HTTPException, status
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
