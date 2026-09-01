"""Request/response shapes for one narrative document and its provenance.

The provenance block mirrors crop_advisor.claims.Claim field-for-field
(crop-climate-advisor/crop_advisor/claims.py:40-47), so a document authored here
maps onto a Claim with no rename table.

Deliberately absent: opt_min / opt_max. Agronomic bands are the advisor's data,
never CMS prose -- model.py:65-67. If a figure's only home is an item body, it
is in the wrong system.

Every length bound mirrors a column in model.py. Without them an over-long value
reaches PostgreSQL, raises DataError, and FastAPI serves HTTP 500; with them the
client gets 422 naming the field.
"""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ItemBase(BaseModel):
    """Fields only -- no cross-field rule.

    Create and Response both derive from this rather than Response inheriting
    Create, so that tightening an inbound rule can never turn a GET of an
    already-stored row into HTTP 500. That is not hypothetical: the database's
    CHECK forbids read_directly=true WITH a via, but permits read_directly=false
    with via=NULL, so a stricter write rule inherited by the response model
    would raise ResponseValidationError on rows the database happily holds.
    """

    sub_category_id: int
    crop_id: int
    # The shared question, e.g. "optimal-temperature". Retrieval returns the
    # whole set for a topic, so contradicting sources arrive together
    # (model.py:90-91). String(128) at model.py:92.
    topic: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=255)  # model.py:93
    body: str = Field(min_length=1)                   # Text, no max
    published: bool = False                           # model.py:95

    # --- provenance, mirroring claims.py:40-47 ------------------------------
    source: str = Field(min_length=1, max_length=255)  # model.py:98
    reference: str = Field(min_length=1)               # Text, no max
    url: str = Field(min_length=1)                     # Text, no max
    read_directly: bool
    via: str | None = None
    condition: str | None = None
    licence_note: str | None = None


class ItemCreate(ItemBase):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sub_category_id": 1,
                "crop_id": 1,
                "topic": "optimal-temperature",
                "title": "Walters & Currey on basil's optimal temperature",
                "body": (
                    "Reports a 29-35 °C optimum, conditioned on a stated daily "
                    "light integral. Does not agree with the ECOCROP band; both "
                    "are published and neither is ranked."
                ),
                "published": False,
                "source": "Walters & Currey (2019)",
                "reference": "Walters & Currey (2019), HortScience 54(11):1915",
                "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10688745/",
                "read_directly": False,
                "via": "Walters, Tarr & Lopez (2023), PLoS One 18(11):e0294905",
                "condition": "DLI 19.5 mol·m⁻²·d⁻¹",
                "licence_note": None,
            }
        }
    )

    @model_validator(mode="after")
    def _read_directly_excludes_via(self) -> "ItemCreate":
        """Mirror of Claim.__post_init__ (claims.py:53-62) and of the database's
        read_directly_excludes_via CHECK (model.py:78-81).

        Without this the row is still rejected -- but by PostgreSQL, as an
        unhandled IntegrityError, which FastAPI serves as HTTP 500. With it the
        client gets 422 naming the rule.
        """
        if self.read_directly and (self.via or "").strip():
            raise ValueError(
                "read_directly=true cannot also name a 'via' source: it would "
                "credit the via paper's URL as read directly and silently drop "
                "the citation chain"
            )
        return self


class ItemResponse(ItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
