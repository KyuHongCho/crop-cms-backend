"""Read-only shape for a crop.

There is deliberately no CropCreate: crops are seeded to match the advisor's
data/ecocrop/<slug>.json rather than authored here, and deleting one cascades
to every document about it (model.py:88). Settled in PR #2.

The four columns map onto that file as: slug <- the filename, common_name <-
"common_name", scientific_name <- the JSON key "name", ecocrop_id <-
"ecocrop_id". Note the JSON has no "slug" key of its own.
"""
from pydantic import BaseModel, ConfigDict


class CropResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "slug": "basil",                        # <- the JSON filename
                "common_name": "basil",                 # <- "common_name"
                "scientific_name": "Ocimum basilicum",  # <- JSON key "name"
                "ecocrop_id": 1547,
            }
        },
    )

    id: int
    slug: str
    common_name: str
    scientific_name: str
    # model.py:20 has no nullable=False, so this one really can be absent.
    ecocrop_id: int | None = None
