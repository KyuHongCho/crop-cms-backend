from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse

from app.router import category, chat, crop, health, item, member, retrieval

app = FastAPI()


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """FastAPI's default 422 echoes each bad value back; a lone surrogate in one
    cannot be encoded as UTF-8, which turned the 422 into a 500. Only then, drop
    the echoed input; every other 422 is the default response, unchanged."""
    try:
        return await request_validation_exception_handler(request, exc)
    except UnicodeEncodeError:
        errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

app.include_router(category.router, tags=["category"])
app.include_router(chat.router, tags=["chat"])
app.include_router(crop.router, tags=["crop"])
app.include_router(health.router, tags=["health"])
app.include_router(item.router, tags=["item"])
app.include_router(member.router, tags=["member"])
app.include_router(retrieval.router, tags=["retrieval"])


@app.get("/")
def read_root():
    return {"Hello": "World"}
