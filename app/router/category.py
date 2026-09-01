from fastapi import APIRouter, HTTPException  # noqa: F401 - used from the CRUD lectures on

router = APIRouter()


# "main category" = kind of knowledge (crop profile, research literature,
# cultivation practice, pests and disorders). A crop is NOT one -- model.py:27.
@router.get("/main-categories")
async def get_main_categories():
    return {"main_categories": "main_categories"}

@router.get("/sub-categories")
async def get_sub_categories():
    return {"sub_categories": "sub_categories"}
