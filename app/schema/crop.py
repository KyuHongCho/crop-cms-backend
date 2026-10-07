"""Read-only shape for a crop.

No CropCreate: crops are seeded from the advisor's data/ecocrop/<slug>.json (slug <- filename,
scientific_name <- key "name"). No delete either: Item.crop_id is ON DELETE RESTRICT.
"""
from pydantic import BaseModel, ConfigDict


class CropResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "slug": "basil",
                "common_name": "basil",
                "scientific_name": "Ocimum basilicum",
                "ecocrop_id": 1547,
            }
        },
    )

    id: int
    slug: str
    common_name: str
    scientific_name: str
    # Crop.ecocrop_id is nullable.
    ecocrop_id: int | None = None
