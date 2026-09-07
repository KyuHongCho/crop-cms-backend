"""Data access for the knowledge taxonomy.

The response shaping lives in app/schema/category.py, reached through
from_attributes and the routers' response_model -- not hand-built here. The
course's crud/category.py:22-38 assembles its response field by field across 17
lines despite setting from_attributes on every class; that work is what
response_model does for free.
"""
from sqlalchemy import func, select
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


async def count_sub_categories(db: AsyncSession, main_category_id: int) -> int:
    """How many sub-categories hang off this main category.

    The router refuses the delete with this number rather than letting
    ON DELETE RESTRICT raise, because the ForeignKeyViolation reaches the client
    as an opaque HTTP 500 -- the same reason the create endpoints pre-check
    their parents (router/category.py:52-55).
    """
    return await db.scalar(
        select(func.count())
        .select_from(model.SubCategory)
        .where(model.SubCategory.main_category_id == main_category_id)
    )


async def delete_main_category(
    db: AsyncSession, main_category: model.MainCategory
) -> None:
    """Delete a main category that the caller has already found to be empty.

    Nothing cascades: MainCategory.subcategories carries no delete-orphan and
    passive_deletes="all" (model.py), so this emits one DELETE and lets
    ON DELETE RESTRICT be the backstop if a child appeared in between.
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

    The refiling itself is NOT done here. The BEFORE DELETE trigger
    refile_items_to_uncategorised() (app/db/migrate_db.py) moves the documents
    to UNCATEGORISED_SUB_CATEGORY_ID inside the same statement, so raw SQL and
    psql get the same behaviour as this endpoint. All this function does is
    count them first, because after the DELETE they are indistinguishable from
    documents that were already in the bucket.

    The count can UNDER-report. Under READ COMMITTED a document inserted between
    the count and the DELETE is refiled by the trigger but not counted;
    SELECT ... FOR UPDATE on the sub-category closes it (a child INSERT takes
    FOR KEY SHARE, which conflicts). Deliberately not paid for: this is a
    single-user CMS with no authentication and no concurrent writer.
    """
    refiled = await db.scalar(
        select(func.count())
        .select_from(model.Item)
        .where(model.Item.sub_category_id == sub_category.id)
    )
    await db.delete(sub_category)
    await db.commit()
    return refiled
