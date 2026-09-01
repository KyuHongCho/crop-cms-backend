from fastapi import APIRouter, HTTPException  # noqa: F401 - used from the CRUD lectures on

router = APIRouter()

@router.get("/items")
async def get_items():
    return {"items": "items"}