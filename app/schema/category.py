"""Request/response shapes for the knowledge taxonomy.

Field names mirror app/model/model.py exactly, so crud can do
`model.MainCategory(**body.model_dump())` with no translation layer -- which is
precisely what a verbatim port of the course's schemas cannot do: its
`subcategory_name` raises TypeError against this model, and its category body
carries no `slug`, which surfaces as a NotNullViolation at commit() rather than
as a validation error at the edge.

Every length bound mirrors a column. Without them an over-long value reaches
PostgreSQL, raises DataError, and FastAPI serves HTTP 500; with them the client
gets 422 naming the field. Response models carry no bounds: their values come
from columns that already enforce them, so a bound there could only ever reject
data this service itself stored.
"""
from pydantic import BaseModel, ConfigDict, Field


class MainCategoryCreate(BaseModel):
    """Kind of knowledge: crop profile, research literature, cultivation
    practice, pests and disorders (model.py:26-28). A crop is NOT one -- it is
    an entity, so adding a crop does not duplicate this tree."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "slug": "research-literature",
                "name": "Research literature",
                "position": 1,
            }
        }
    )

    # NOT NULL with no server default (model.py:33). Omit it and the failure is
    # a NotNullViolation at commit(), not a validation error at the boundary.
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    position: int = 0  # mirrors server_default=text("0"), model.py:35


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
    # at model.py:45.
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


class MainCategoryResponse(BaseModel):
    """`subcategories` REQUIRES selectinload() in crud.

    A lazy load under the async session raises MissingGreenlet, and pydantic
    wraps it in a ValidationError -- so `except MissingGreenlet` does not catch
    it. This fires consistently, including in the session that just created the
    child row, because expire_on_commit=False means the relationship was simply
    never loaded.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    position: int
    # `= []` is safe here: pydantic deep-copies field defaults, so two
    # instances do not share one list.
    subcategories: list[SubCategoryResponse] = []
