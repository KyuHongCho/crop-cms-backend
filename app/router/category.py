from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.category as category_crud
import app.model.model as model
import app.schema.category as category_schema
from app.db.db import get_db

router = APIRouter()


# "main category" = kind of knowledge (crop profile, research literature,
# cultivation practice, pests and disorders). A crop is NOT one -- model.py:27.
@router.get(
    "/main-categories",
    response_model=list[category_schema.MainCategoryResponse],
)
async def get_main_categories(db: AsyncSession = Depends(get_db)):
    return await category_crud.get_main_categories(db)


@router.post(
    "/main-categories",
    response_model=category_schema.MainCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_main_category(
    body: category_schema.MainCategoryCreate,
    db: AsyncSession = Depends(get_db),
):
    return await category_crud.create_main_category(db, body)


@router.get(
    "/sub-categories",
    response_model=list[category_schema.SubCategoryResponse],
)
async def get_sub_categories(db: AsyncSession = Depends(get_db)):
    return await category_crud.get_sub_categories(db)


@router.post(
    "/sub-categories",
    response_model=category_schema.SubCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sub_category(
    body: category_schema.SubCategoryCreate,
    db: AsyncSession = Depends(get_db),
):
    # Checked here rather than left to the foreign key: the FK violation is an
    # IntegrityError, which reaches the client as an opaque HTTP 500.
    if not await db.get(model.MainCategory, body.main_category_id):
        raise HTTPException(status_code=404, detail="Main category not found")
    return await category_crud.create_sub_category(db, body)
