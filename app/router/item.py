from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.item as item_crud
import app.model.model as model
import app.schema.item as item_schema
from app.auth.dependency import bearer_scheme, get_current_member, require_editor
from app.db.db import get_db

router = APIRouter()


@router.get("/items", response_model=list[item_schema.ItemResponse])
async def get_items(
    # not named `status`: that would shadow fastapi.status, used below.
    status_filter: Literal["published", "all"] = Query("published", alias="status"),
    # default equals max: the Library fetches everything with no parameters and has no paging.
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0, le=item_schema.MAX_OFFSET),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """Published documents; `status=all` adds drafts and needs an editor or admin token.

    The default path ignores the token: the frontend sends one on every request, and a 401 on it
    would end the session."""
    if status_filter == "published":
        return await item_crud.get_items(db, limit=limit, offset=offset)
    if credentials is None:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    member = await get_current_member(credentials, db)
    await require_editor(member)
    return await item_crud.get_items(db, include_unpublished=True, limit=limit, offset=offset)


@router.post(
    "/items",
    dependencies=[Depends(require_editor)],
    response_model=item_schema.ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    body: item_schema.ItemCreate,
    db: AsyncSession = Depends(get_db),
):
    # checked here: an FK violation would reach the client as an opaque 500
    # (as in create_sub_category).
    if not await db.get(model.SubCategory, body.sub_category_id):
        raise HTTPException(status_code=404, detail="Sub-category not found")
    if not await db.get(model.Crop, body.crop_id):
        raise HTTPException(status_code=404, detail="Crop not found")
    return await item_crud.create_item(db, body)


@router.delete(
    "/items/{item_id}",
    dependencies=[Depends(require_editor)],
    response_model=item_schema.ItemResponse,
)
async def delete_item(
    # not ge=1: an unknown id is a plain 404 here as on the category routes, 0 and negatives
    # included. Only ids outside int4, which Postgres cannot compare, are a 422.
    item_id: int = Path(ge=item_schema.MIN_INT4, le=item_schema.MAX_INT4),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a document and its chunks; 200 returns the deleted row, since there is no
    backup or audit record to recover it from."""
    item = await item_crud.delete_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
