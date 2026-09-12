"""App entrypoint: creates the FastAPI app and registers every API slice's router.

To add a new API:
1. Create app/apis/<YourApiName>/ with api.py, service.py, repository.py, schema.py.
2. Import its router below and add it with app.include_router(...).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.apis.CreateUser.api import router as create_user_router
from app.apis.GetListUser.api import router as get_list_user_router
from app.apis.GetUserById.api import router as get_user_by_id_router
from app.common.config import settings
from app.common.database import Base, engine
from app.common.exceptions import register_exception_handlers

# Creates tables on startup for local/dev convenience (sqlite by default).
# Use a real migration tool (e.g. Alembic) instead of this for production.
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(get_list_user_router)
app.include_router(get_user_by_id_router)
app.include_router(create_user_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"success": True, "message": "OK", "data": {"status": "healthy"}}
