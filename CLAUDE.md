# CLAUDE.md

Guidance for Claude Code (or any AI assistant) working in this repository.

## What this project is

A Python/FastAPI backend using **vertical slice architecture**: every API
endpoint lives in its own folder under `app/apis/`, and each of those
folders is internally split into the same 3 layers. Code that is truly
shared by multiple slices lives in `app/common/`.

## Folder layout

```
app/
  common/                 # shared across the whole project
    config.py             # env-based settings (Settings, get_settings)
    database.py            # SQLAlchemy engine/session, Base, get_db()
    models.py              # ORM models used by more than one slice
    response.py            # ApiResponse envelope + success_response()
    exceptions.py          # AppError, NotFoundError, ConflictError, handlers
    logger.py              # get_logger()
  apis/
    GetListUser/           # one folder = one API
      api.py                # layer 1: HTTP route (FastAPI router)
      service.py            # layer 2: business logic
      repository.py         # layer 3: data access (DB queries)
      schema.py             # pydantic request/response models
    CreateUser/             # same 3-layer shape
    GetUserById/            # same 3-layer shape
  main.py                  # creates the app, registers every slice's router
```

## The 3-layer rule inside every API folder

1. **`api.py`** — FastAPI `APIRouter`. Parses the HTTP request, calls the
   service, returns the response. No business logic, no DB queries here.
2. **`service.py`** — business logic: validation, orchestration, raising
   `AppError` subclasses on invalid states. Talks only to the repository
   (and other services if needed), never to the DB or to FastAPI directly.
3. **`repository.py`** — data access only. Raw SQLAlchemy queries. No
   business rules here — if a query result being empty is invalid, that
   decision belongs in the service, not the repository.

`schema.py` holds the pydantic models for that one API's request/response
shapes. Keep them local to the slice unless a shape is reused elsewhere.

## Adding a new API

To add e.g. `DeleteUser`:

1. `mkdir app/apis/DeleteUser`
2. Add `__init__.py`, `schema.py`, `repository.py`, `service.py`, `api.py`,
   following the pattern in `app/apis/GetUserById/` (simplest example).
3. In `app/main.py`, import the new router and add
   `app.include_router(delete_user_router)`.
4. If the API needs a DB model that's already used elsewhere, import it
   from `app/common/models.py`. Only add a new model there if more than
   one slice will need it — otherwise define it locally.

Each API folder is self-contained: it should be possible to delete one
folder under `app/apis/` without breaking any other API.

## Conventions

- One API = one folder. Folder name is PascalCase and describes the
  action (`GetListUser`, `CreateUser`, `DeleteUser`, `UpdateUserProfile`).
- Every route returns the `ApiResponse` envelope
  (`{"success": bool, "message": str, "data": ...}`) via
  `app.common.response.success_response()`.
- Raise `app.common.exceptions.AppError` (or `NotFoundError` /
  `ConflictError`) from the service layer for expected failure cases
  instead of raising `HTTPException` directly — the global handler in
  `app/common/exceptions.py` converts it to the standard error shape.
- Use `app.common.logger.get_logger(__name__)` for logging, not `print`.
- Don't add cross-slice imports between two `app/apis/*` folders. If two
  slices need to share logic, move that logic into `app/common/`.

## Running things

- Install: `pip install -r requirements-dev.txt`
- Run locally: `uvicorn app.main:app --reload`
- Run tests: `pytest`
- Run with Docker: `docker compose up --build`

See [README.md](README.md) for full setup/usage instructions.
