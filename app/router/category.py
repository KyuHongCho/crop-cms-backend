from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.category as category_crud
import app.model.model as model
import app.schema.category as category_schema
from app.db.db import get_db

router = APIRouter()


async def _raise_from_integrity_error(db: AsyncSession, exc: IntegrityError) -> None:
    """Discriminate the SQLSTATE. Never returns normally -- always raises.

    23505 (unique_violation) -> 409, naming the constraint that actually
    fired via exc.orig.diag.constraint_name. A blanket `IntegrityError -> 409`
    would report a desynced sequence's pkey collision (constraint
    "main_categories_pkey") as "a category with that slug already exists"
    (constraint "main_categories_slug_key") -- see plan-2's "Preconditions on
    the sibling plan" #1. Naming the real constraint is what tells the two
    apart.

    23514 (check_violation) -> 422, kept as a documented drift backstop, like
    the P0001 handler in delete_sub_category below. Neither category table
    carries a CHECK constraint today, so this branch is not reachable through
    this router -- it exists for the day one is added here, the way
    schema/item.py:ItemCreate's own validator already returns 422 for
    Item.read_directly_excludes_via before the database is ever asked.

    Anything else is re-raised untouched: a real bug (a typo'd column, a
    missing table) must stay a 500, not be dressed up as a conflict.
    """
    await db.rollback()
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        constraint_name = getattr(exc.orig.diag, "constraint_name", None)
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"a unique constraint was violated: {constraint_name}",
                "constraint_name": constraint_name,
            },
        ) from exc
    if sqlstate == "23514":
        raise HTTPException(status_code=422, detail=exc.orig.diag.message_primary) from exc
    raise exc


# "main category" = kind of knowledge (crop profile, research literature,
# cultivation practice, pests and disorders). A crop is NOT one -- see
# MainCategory's docstring in model.py.
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
    try:
        return await category_crud.create_main_category(db, body)
    except IntegrityError as exc:
        await _raise_from_integrity_error(db, exc)


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
    try:
        return await category_crud.create_sub_category(db, body)
    except IntegrityError as exc:
        await _raise_from_integrity_error(db, exc)


@router.delete(
    "/main-categories/{main_category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_main_category(
    main_category_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete an EMPTY main category. One with sub-categories is refused.

    Refused rather than cascaded: deleting "research literature" should not
    quietly take every document filed beneath it. Delete the sub-categories
    first -- each of those refiles its documents instead of destroying them.
    """
    main_category = await db.get(model.MainCategory, main_category_id)
    if not main_category:
        raise HTTPException(status_code=404, detail="Main category not found")

    # Counted here so the 409 can say how many. Left to the database, it would
    # be a foreign-key error -- an opaque 500 (as with the create endpoint).
    children = await category_crud.count_sub_categories(db, main_category_id)
    if children:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Main category still holds {children} "
                f"sub-categor{'y' if children == 1 else 'ies'}; "
                "delete them first -- each one refiles its documents rather "
                "than destroying them"
            ),
        )

    await category_crud.delete_main_category(db, main_category)


@router.delete(
    "/sub-categories/{sub_category_id}",
    response_model=category_schema.SubCategoryDeleteResponse,
)
async def delete_sub_category(
    sub_category_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a sub-category. Its documents are REFILED, never destroyed.

    200 with a count, not an empty 204: the documents move, and the caller
    should be told where. The move is done by a trigger in the database, so
    psql behaves the same way -- see app/db/migrate_db.py.
    """
    sub_category = await db.get(model.SubCategory, sub_category_id)
    if not sub_category:
        raise HTTPException(status_code=404, detail="Sub-category not found")

    # The bucket is where everything else gets refiled TO. Without it, a later
    # delete fails on a foreign key naming `items` -- a table the caller never
    # touched. Checked here for a readable message; the trigger refuses it
    # again anyway, for callers that never run this code.
    if sub_category_id == model.UNCATEGORISED_SUB_CATEGORY_ID:
        raise HTTPException(
            status_code=409,
            detail=(
                'the "Uncategorised" sub-category cannot be deleted: it is '
                "where documents from deleted sub-categories are refiled to"
            ),
        )

    try:
        refiled = await category_crud.delete_sub_category(db, sub_category)
    except ProgrammingError as exc:
        # P0001 is the code PostgreSQL gives a trigger's own RAISE. Narrow on
        # purpose: ProgrammingError also covers real bugs, like a typo'd query
        # or a missing column, and dressing those up as an ordinary conflict is
        # worse than a 500. Everything else is re-raised untouched. (Duplicate
        # slugs and the like are IntegrityError, which never reaches here.)
        if getattr(exc.orig, "sqlstate", None) != "P0001":
            raise
        await db.rollback()
        raise HTTPException(status_code=409, detail=exc.orig.diag.message_primary) from exc

    return category_schema.SubCategoryDeleteResponse(
        documents_refiled=refiled,
        refiled_to=model.UNCATEGORISED_SUB_CATEGORY_ID,
    )
