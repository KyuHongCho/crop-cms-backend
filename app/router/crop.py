from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.crop as crop_crud
import app.schema.crop as crop_schema
from app.db.db import get_db

router = APIRouter()


# a crop is an entity, not a category (see MainCategory in model.py). GET only: crops mirror
# the advisor's ECOCROP data and Item.crop_id is ON DELETE RESTRICT (app/schema/crop.py).
@router.get("/crops", response_model=list[crop_schema.CropResponse])
async def get_crops(db: AsyncSession = Depends(get_db)):
    return await crop_crud.get_crops(db)
