from fastapi import FastAPI

from app.router import category, crop, item

app = FastAPI()

app.include_router(category.router, tags=["category"])
app.include_router(crop.router, tags=["crop"])
app.include_router(item.router, tags=["item"])


@app.get("/")
def read_root():
    return {"Hello": "World"}
