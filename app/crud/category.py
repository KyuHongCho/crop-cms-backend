"""Data access for the knowledge taxonomy.

No response is built field by field here. Where a function returns ORM objects,
the routers' response_model turns them into JSON through the schemas in
app/schema/category.py (from_attributes).
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.model.model as model
import app.schema.category as category_schema


async def get_main_categories(db: AsyncSession) -> list[model.MainCategory]:
    """selectinload is mandatory, not an optimisation.

    See MainCategoryResponse's docstring (app/schema/category.py) for why a
    lazy load under the async session would turn this into an HTTP 500.
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
    # expire_on_commit=False in db.py keeps the scalar columns readable here
    # without a refresh -- but .subcategories was never loaded, and
    # MainCategoryResponse reads it. Without this line the create returns 500.
    await db.refresh(main_category, ["subcategories"])
    return main_category


async def count_sub_categories(db: AsyncSession, main_category_id: int) -> int:
    """How many sub-categories hang off this main category.

    The router refuses the delete with this number rather than letting the
    database raise, whose foreign-key error would reach the client as an opaque
    500 -- the same reason the create endpoints pre-check their parents.
    """
    return await db.scalar(
        select(func.count())
        .select_from(model.SubCategory)
        .where(model.SubCategory.main_category_id == main_category_id)
    )


async def delete_main_category(
    db: AsyncSession, main_category: model.MainCategory
) -> None:
    """Delete a main category the caller has already found to be empty.

    Nothing cascades (see model.py), so this is a single DELETE. If a child
    appeared in the meantime, ON DELETE RESTRICT stops it.
    """
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

    The refiling happens in the trigger in app/db/migrate_db.py, not here:
    it moves the documents as part of the same DELETE, so psql behaves the
    same way. This only adds the count, taken first, since afterwards the
    moved documents look identical to ones already in the bucket.

    The count can under-report a document inserted between the count and
    the DELETE -- refiled but not counted. Locking the sub-category first
    (SELECT ... FOR UPDATE) would close that gap but is not worth it here:
    this is a single-user CMS with no concurrent writer.
    """
    refiled = await db.scalar(
        select(func.count())
        .select_from(model.Item)
        .where(model.Item.sub_category_id == sub_category.id)
    )
    await db.delete(sub_category)
    await db.commit()
    return refiled
