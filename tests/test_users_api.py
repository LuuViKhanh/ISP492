"""End-to-end tests for the user API slices, using an isolated in-memory SQLite DB."""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.common.database import Base, get_db
from app.main import app

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_create_and_get_user():
    create_resp = client.post("/users", json={"full_name": "Ada Lovelace", "email": "ada@example.com"})
    assert create_resp.status_code == 201
    user_id = create_resp.json()["data"]["id"]

    get_resp = client.get(f"/users/{user_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["email"] == "ada@example.com"


def test_create_user_duplicate_email_conflicts():
    client.post("/users", json={"full_name": "Grace Hopper", "email": "grace@example.com"})
    resp = client.post("/users", json={"full_name": "Grace H.", "email": "grace@example.com"})
    assert resp.status_code == 409


def test_get_list_user():
    resp = client.get("/users", params={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] >= 1


def test_get_user_by_id_not_found():
    resp = client.get("/users/999999")
    assert resp.status_code == 404
