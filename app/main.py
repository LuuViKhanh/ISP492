import os
import sys

# Thêm thư mục gốc "project" vào Python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.database.db import async_engine, Base
import app.modules.auth.models  # noqa: F401 — đảm bảo model được load trước create_all

# Import routers từ các module
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.missions.router import router as missions_router
from app.modules.fleet.router import router as fleet_router
from app.modules.ai_predictions.router import router as ai_router

# Developer Database API (dùng chung server, prefix /dev/db/...)
from app.database.router import router as db_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version="1.0.0", routes=app.routes)
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    for path in schema["paths"].values():
        for method in path.values():
            method["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(users_router, prefix=settings.API_V1_STR)
app.include_router(missions_router, prefix=settings.API_V1_STR)
app.include_router(fleet_router, prefix=settings.API_V1_STR)
app.include_router(ai_router, prefix=settings.API_V1_STR)
app.include_router(db_router)  # Dev DB API — không dùng API prefix, truy cập tại /dev/db/...

# Tự động include các router của các roles
import importlib
for mod in ["auth", "users", "missions", "fleet", "ai_predictions"]:
    for role in ["admin", "operator", "technician", "customer"]:
        try:
            router_mod = importlib.import_module(f"app.modules.{mod}.{role}.router")
            app.include_router(router_mod.router, prefix=settings.API_V1_STR)
        except ImportError:
            pass


@app.get("/")
def root():
    return {
        "message": "Welcome to DroneOptAI API Gateway"
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )