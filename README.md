# Drone-BE

A Python backend API built with **FastAPI**, using a **vertical slice
architecture**: each API endpoint gets its own folder, and every folder is
split into the same 3 clean layers (api → service → repository).

## Architecture

```
app/
  common/            # shared code used by the whole project (config, DB, logger, response envelope, exceptions)
  apis/
    GetListUser/     # GET /users            (list, paginated)
    GetUserById/     # GET /users/{id}
    CreateUser/      # POST /users
  main.py            # app factory, registers every slice's router
```

Each `app/apis/<ApiName>/` folder contains:

| File            | Layer               | Responsibility                                  |
|-----------------|----------------------|--------------------------------------------------|
| `api.py`        | API (presentation)  | FastAPI route, request/response wiring only      |
| `service.py`    | Service (business)  | Validation, business rules, orchestration        |
| `repository.py` | Repository (data)   | DB queries only, no business rules                |
| `schema.py`     | -                    | Pydantic request/response models for this API    |

This keeps every API independent and easy to reason about: to understand
or change one endpoint, you only ever need to open one folder. See
[CLAUDE.md](CLAUDE.md) for the full contribution/pattern guide, including
how to add a new API.

## Requirements

- Python 3.12+
- (Optional) Docker + Docker Compose

## Setup (local, no Docker)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env
```

Run the server:

```bash
uvicorn app.main:app --reload
```

The API is now at `http://localhost:8000`. Interactive docs:
`http://localhost:8000/docs`.

## Run with Docker

```bash
docker compose up --build
```

This builds the image from the `Dockerfile` and starts the API on
`http://localhost:8000`. By default it uses SQLite (`drone.db`, created
automatically). To use Postgres or another DB instead, set `DATABASE_URL`
in `docker-compose.yml` (or your `.env`) to a SQLAlchemy connection string,
e.g.:

```
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
```

(Install the matching driver, e.g. `psycopg2-binary`, in `requirements.txt`
if you switch to Postgres.)

## Running tests

```bash
pytest
```

Tests spin up an isolated in-memory SQLite database and hit the routes
through FastAPI's `TestClient`, so they don't touch your local `drone.db`.

## Example endpoints

| Method | Path            | Description             |
|--------|-----------------|--------------------------|
| GET    | `/health`       | Health check             |
| GET    | `/users`        | List users (paginated)   |
| GET    | `/users/{id}`   | Get a single user by id  |
| POST   | `/users`        | Create a user            |

Every response follows the same envelope:

```json
{
  "success": true,
  "message": "OK",
  "data": { ... }
}
```

## Adding a new API

1. Create `app/apis/<YourApiName>/` with `api.py`, `service.py`,
   `repository.py`, `schema.py` (copy the shape of `app/apis/GetUserById/`
   as a starting template).
2. Register its router in `app/main.py`.
3. Add DB models that are shared by more than one API to
   `app/common/models.py`; keep single-slice models local to that slice.

Full conventions are documented in [CLAUDE.md](CLAUDE.md).
