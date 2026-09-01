from fastapi import APIRouter, HTTPException  # noqa: F401 - used from the CRUD lectures on

router = APIRouter()


# A crop is an entity, not a category (model.py:27-28). Its four columns mirror
# FAO ECOCROP -- the same fields the advisor's scripts/scrape_ecocrop.py writes.
# GET only: crops are seeded to match the advisor's data/ecocrop/<slug>.json
# rather than authored here, and deleting one cascades to all its documents.
@router.get("/crops")
async def get_crops():
    return {"crops": "crops"}
