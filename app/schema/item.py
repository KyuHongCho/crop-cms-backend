"""Request/response shapes for one narrative document and its provenance.

Provenance mirrors crop_advisor.claims.Claim. No opt_min/opt_max: bands are the advisor's data.
Length bounds mirror model.py columns, so an over-long value is a 422, not a DataError 500.
"""
from pydantic import BaseModel, ConfigDict, Field, model_validator

# int4 bounds: a path id outside them makes Postgres raise DataError (a 500), so the router 422s it.
MIN_INT4 = -(2**31)
MAX_INT4 = 2**31 - 1
MAX_OFFSET = 2**63 - 1  # the ?offset query parameter; OFFSET is a PostgreSQL bigint


class ItemBase(BaseModel):
    """Fields only, no cross-field rule.

    Response derives from this, not from Create: the DB CHECK permits read_directly=false
    with via=NULL, so a stricter inherited write rule would 500 on rows the database holds.
    """

    sub_category_id: int
    crop_id: int
    # mirrors Item.topic (String(128)); retrieval groups by it.
    topic: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)  # Text, no max
    published: bool = False

    # --- provenance, mirroring claims.py's Claim ---
    source: str = Field(min_length=1, max_length=255)
    reference: str = Field(min_length=1)  # Text, no max
    url: str = Field(min_length=1)
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
        """Mirrors Claim.__post_init__ and the read_directly_excludes_via CHECK: gives a 422
        naming the rule instead of an unhandled IntegrityError (500)."""
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
