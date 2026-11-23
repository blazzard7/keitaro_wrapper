from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.errors import DomainError, KeitaroError, NotFoundError, WriteDisabledError
from app.routers import campaigns, keitaro, streams

app = FastAPI(title="Keitaro Wrapper")
app.include_router(keitaro.router)
app.include_router(campaigns.router)
app.include_router(streams.router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

_CODES = {DomainError: 400, NotFoundError: 404, WriteDisabledError: 403, KeitaroError: 502}


def _handler(code: int):
    async def handler(_: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=code)
    return handler


for exc_type, code in _CODES.items():
    app.add_exception_handler(exc_type, _handler(code))


@app.get("/", include_in_schema=False)
def index():
    return FileResponse("app/static/index.html")
