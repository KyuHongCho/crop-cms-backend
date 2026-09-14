from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.crop as crop_crud
import app.schema.crop as crop_schema
from app.db.db import get_db

router = APIRouter()


# A crop is an entity, not a category -- see MainCategory's docstring in
# model.py. GET only, and Item.crop_id is ON DELETE RESTRICT -- see
# app/schema/crop.py's module docstring for why (crops mirror the advisor's
# ECOCROP data) and CropResponse for the column-to-JSON-field mapping.
@router.get("/crops", response_model=list[crop_schema.CropResponse])
async def get_crops(db: AsyncSession = Depends(get_db)):
    return await crop_crud.get_crops(db)
