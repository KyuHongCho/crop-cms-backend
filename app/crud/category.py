"""Data access for the knowledge taxonomy; returns ORM objects that the routers'
response_model serialises via app/schema/category.py (from_attributes).
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.model.model as model
import app.schema.category as category_schema


async def get_main_categories(db: AsyncSession) -> list[model.MainCategory]:
    """selectinload is mandatory: a lazy load under the async session becomes a 500
    (see MainCategoryResponse in app/schema/category.py)."""
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
    # expire_on_commit=False keeps scalars readable, but .subcategories was never loaded
    # and MainCategoryResponse reads it; without this refresh the create returns 500.
    await db.refresh(main_category, ["subcategories"])
    return main_category


async def count_sub_categories(db: AsyncSession, main_category_id: int) -> int:
    """How many sub-categories hang off this main category (so the 409 can say how many,
    instead of the FK error reaching the client as a 500)."""
    return await db.scalar(
        select(func.count())
        .select_from(model.SubCategory)
        .where(model.SubCategory.main_category_id == main_category_id)
    )


async def delete_main_category(
    db: AsyncSession, main_category: model.MainCategory
) -> None:
    """Delete a main category the caller found empty; a child appearing meanwhile is
    stopped by ON DELETE RESTRICT."""
    await db.delete(main_category)
    await db.commit()


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


async def delete_sub_category(
    db: AsyncSession, sub_category: model.SubCategory
) -> int:
    """Delete a sub-category and report how many documents were refiled.

    The trigger refiles; the count is taken first (afterwards moved documents look like bucket
    ones). It can under-report an insert in between; no lock in this single-user CMS.
    """
    refiled = await db.scalar(
        select(func.count())
        .select_from(model.Item)
        .where(model.Item.sub_category_id == sub_category.id)
    )
    await db.delete(sub_category)
    await db.commit()
    return refiled
