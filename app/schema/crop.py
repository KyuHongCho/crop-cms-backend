"""Read-only shape for a crop.

There is deliberately no CropCreate: crops are seeded to match the advisor's
data/ecocrop/<slug>.json rather than authored here. Settled in PR #2. There is
no delete either, and the database backs that up -- Item.crop_id is ON DELETE
RESTRICT, so a crop that still has documents about it cannot be removed.

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
    # Crop.ecocrop_id has no nullable=False, so this one really can be absent.
    ecocrop_id: int | None = None
