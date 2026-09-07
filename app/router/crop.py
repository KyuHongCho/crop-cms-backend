from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.crop as crop_crud
import app.schema.crop as crop_schema
from app.db.db import get_db

router = APIRouter()


# A crop is an entity, not a category -- see MainCategory's docstring in
# model.py. Its four columns mirror FAO ECOCROP -- the same fields the
# advisor's scripts/scrape_ecocrop.py writes. GET only: crops are seeded to
# match the advisor's data/ecocrop/<slug>.json rather than authored here, and a
# crop that still has documents cannot be deleted (Item.crop_id is ON DELETE
# RESTRICT).
@router.get("/crops", response_model=list[crop_schema.CropResponse])
async def get_crops(db: AsyncSession = Depends(get_db)):
    return await crop_crud.get_crops(db)
