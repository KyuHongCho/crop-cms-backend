"""Request/response shapes for the knowledge taxonomy.

Field names match model.py so crud can do `model.X(**body.model_dump())`. Length limits copy
the column sizes: 422 naming the field instead of a PostgreSQL 500 (responses need none).
"""
from pydantic import BaseModel, ConfigDict, Field


class MainCategoryCreate(BaseModel):
    """Kind of knowledge: crop profile, research literature, cultivation
    practice, pests and disorders. A crop is not a category -- crops have
    their own table, so adding one does not require copying this tree (the seed
    still files each crop under its own main category)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "slug": "research-literature",
                "name": "Research literature",
                "position": 1,
            }
        }
    )

    # required: the column is NOT NULL with no default, so a missing slug is a 422, not a commit() error.
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    position: int = 0  # mirrors server_default=text("0")


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
    # unique per parent, not globally (UniqueConstraint on SubCategory).
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
    # so the caller can find the moved documents without knowing the bucket id.
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
    # `= []` is safe: pydantic deep-copies field defaults.
    subcategories: list[SubCategoryResponse] = []
