from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.category as category_crud
import app.model.model as model
import app.schema.category as category_schema
from app.auth.dependency import require_editor
from app.db.db import get_db

router = APIRouter()


async def _raise_from_integrity_error(db: AsyncSession, exc: IntegrityError) -> None:
    """Map an IntegrityError to an HTTP error by SQLSTATE; always raises.

    23505 -> 409 naming the constraint (a blanket 409 would hide a pkey collision as a duplicate
    slug); 23514 -> 422 backstop for a future CHECK. Anything else is re-raised, staying a 500.
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


# "main category" = kind of knowledge (see MainCategory in model.py); a crop is not one.
@router.get(
    "/main-categories",
    response_model=list[category_schema.MainCategoryResponse],
)
async def get_main_categories(db: AsyncSession = Depends(get_db)):
    return await category_crud.get_main_categories(db)


@router.post(
    "/main-categories",
    dependencies=[Depends(require_editor)],
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
    dependencies=[Depends(require_editor)],
    response_model=category_schema.SubCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sub_category(
    body: category_schema.SubCategoryCreate,
    db: AsyncSession = Depends(get_db),
):
    # checked here: the FK violation would reach the client as an opaque 500.
    if not await db.get(model.MainCategory, body.main_category_id):
        raise HTTPException(status_code=404, detail="Main category not found")
    try:
        return await category_crud.create_sub_category(db, body)
    except IntegrityError as exc:
        await _raise_from_integrity_error(db, exc)


@router.delete(
    "/main-categories/{main_category_id}",
    dependencies=[Depends(require_editor)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_main_category(
    main_category_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete an empty main category. One with sub-categories is refused.

    Refused rather than cascaded: deleting "research literature" should not
    quietly take every document filed beneath it. Delete the sub-categories
    first -- each of those refiles its documents instead of destroying them.
    """
    main_category = await db.get(model.MainCategory, main_category_id)
    if not main_category:
        raise HTTPException(status_code=404, detail="Main category not found")

    # counted here so the 409 can say how many; the FK error would be an opaque 500.
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
    dependencies=[Depends(require_editor)],
    response_model=category_schema.SubCategoryDeleteResponse,
)
async def delete_sub_category(
    sub_category_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a sub-category. Its documents are refiled, never destroyed.

    200 with a count, not an empty 204: the documents move, and the caller
    should be told where. The move is done by a trigger in the database, so
    psql behaves the same way.
    """
    sub_category = await db.get(model.SubCategory, sub_category_id)
    if not sub_category:
        raise HTTPException(status_code=404, detail="Sub-category not found")

    # the bucket is where everything else is refiled TO; deleting it would break a later
    # delete on a FK naming `items`. The trigger refuses it too, for callers outside this code.
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
        # P0001 is a trigger's own RAISE. Narrow on purpose: ProgrammingError also covers
        # real bugs (typo'd query, missing column), which must stay a 500.
        if getattr(exc.orig, "sqlstate", None) != "P0001":
            raise
        await db.rollback()
        raise HTTPException(status_code=409, detail=exc.orig.diag.message_primary) from exc

    return category_schema.SubCategoryDeleteResponse(
        documents_refiled=refiled,
        refiled_to=model.UNCATEGORISED_SUB_CATEGORY_ID,
    )
