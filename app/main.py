from fastapi import FastAPI

from app.router import category, chat, crop, item, member, retrieval

app = FastAPI()

app.include_router(category.router, tags=["category"])
app.include_router(chat.router, tags=["chat"])
app.include_router(crop.router, tags=["crop"])
app.include_router(item.router, tags=["item"])
app.include_router(member.router, tags=["member"])
app.include_router(retrieval.router, tags=["retrieval"])


@app.get("/")
def read_root():
    return {"Hello": "World"}
