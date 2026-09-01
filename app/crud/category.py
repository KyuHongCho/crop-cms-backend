"""Data access for the knowledge taxonomy.

The response shaping lives in app/schema/category.py, reached through
from_attributes and the routers' response_model -- not hand-built here. The
course's crud/category.py:22-38 assembles its response field by field across 17
lines despite setting from_attributes on every class; that work is what
response_model does for free.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.model.model as model
import app.schema.category as category_schema


async def get_main_categories(db: AsyncSession) -> list[model.MainCategory]:
    """selectinload is mandatory, not an optimisation.

    MainCategoryResponse reads .subcategories; a lazy load under the async
    session raises MissingGreenlet, which pydantic wraps in a ValidationError
    and FastAPI serves as HTTP 500.
    """
    result = await db.execute(
        select(model.MainCategory)
        .options(selectinload(model.MainCategory.subcategories))
        .order_by(model.MainCategory.position, model.MainCategory.id)
    )
    return list(result.scalars().all())


async def create_main_category(
    db: AsyncSession, body: category_schema.MainCategoryCreate
) -> model.MainCategory:
    main_category = model.MainCategory(**body.model_dump())
    db.add(main_category)
    await db.commit()
    # expire_on_commit=False (db.py:19) keeps the scalar columns readable here
    # without a refresh -- but .subcategories was never loaded, and
    # MainCategoryResponse reads it. Without this line the create returns 500.
    await db.refresh(main_category, ["subcategories"])
    return main_category


async def get_sub_categories(db: AsyncSession) -> list[model.SubCategory]:
    result = await db.execute(
        select(model.SubCategory).order_by(
            model.SubCategory.main_category_id,
            model.SubCategory.position,
            model.SubCategory.id,
        )
    )
    return list(result.scalars().all())


async def create_sub_category(
    db: AsyncSession, body: category_schema.SubCategoryCreate
) -> model.SubCategory:
    sub_category = model.SubCategory(**body.model_dump())
    db.add(sub_category)
    await db.commit()
    return sub_category
