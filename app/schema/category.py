"""Request/response shapes for the knowledge taxonomy.

Field names match app/model/model.py exactly, so crud can build a model straight
from a request body -- `model.MainCategory(**body.model_dump())` -- with no
renaming in between.

Length limits copy the column sizes, so an over-long value is rejected with a
422 naming the field instead of failing inside PostgreSQL as a 500. Response
models have no limits: their values already came out of those columns.
"""
from pydantic import BaseModel, ConfigDict, Field


class MainCategoryCreate(BaseModel):
    """Kind of knowledge: crop profile, research literature, cultivation
    practice, pests and disorders. A crop is not a category -- crops have
    their own table, so adding one does not copy this tree."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "slug": "research-literature",
                "name": "Research literature",
                "position": 1,
            }
        }
    )

    # Required: the column is NOT NULL with no default, so a missing slug is a
    # 422 here rather than a database error at commit().
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    position: int = 0  # mirrors MainCategory.position server_default=text("0")


class SubCategoryCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "main_category_id": 1,
                "slug": "temperature-response",
                "name": "Temperature response",
                "position": 1,
            }
        }
    )

    main_category_id: int
    # Unique per parent, not globally: UniqueConstraint(main_category_id, slug)
    # on SubCategory.__table_args__.
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    position: int = 0


class SubCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    main_category_id: int
    slug: str
    name: str
    position: int


class SubCategoryDeleteResponse(BaseModel):
    """What the delete did: its documents were moved, not destroyed.

    A trigger refiles them to "Uncategorised", so the endpoint answers 200 with
    the count instead of an empty 204 that would hide the move.
    """

    documents_refiled: int
    # Returned so the caller can find the moved documents without knowing the
    # bucket's id in advance.
    refiled_to: int


class MainCategoryResponse(BaseModel):
    """`subcategories` must be loaded up front with selectinload() in crud.

    Async SQLAlchemy cannot lazy-load a relationship on first access: it raises
    MissingGreenlet, which pydantic wraps in a ValidationError, so the request
    fails with a 500 (and `except MissingGreenlet` would not catch it).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    position: int
    # `= []` is safe here: pydantic deep-copies field defaults, so two
    # instances do not share one list.
    subcategories: list[SubCategoryResponse] = []
