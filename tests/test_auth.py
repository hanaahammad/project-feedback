import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import User


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.session_local = TestingSessionLocal
        yield test_client
    app.dependency_overrides.clear()


def test_signup_creates_user(client):
    response = client.post("/auth/signup", json={"email": "a@example.com", "password": "hunter2"})
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "a@example.com"
    assert "password" not in body
    assert "password_hash" not in body


def test_signup_rejects_duplicate_email(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": "hunter2"})
    response = client.post("/auth/signup", json={"email": "a@example.com", "password": "different"})
    assert response.status_code == 400


def test_signup_stores_password_hashed(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": "hunter2"})
    with client.session_local() as db:
        user = db.query(User).filter(User.email == "a@example.com").first()
        assert user.password_hash != "hunter2"
        assert user.password_hash.startswith("$2b$")


def test_login_with_correct_credentials_returns_token(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": "hunter2"})
    response = client.post("/auth/login", json={"email": "a@example.com", "password": "hunter2"})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


def test_login_with_wrong_password_is_rejected(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": "hunter2"})
    response = client.post("/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert response.status_code == 401


def test_login_with_unknown_email_is_rejected(client):
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "hunter2"})
    assert response.status_code == 401


def test_protected_route_without_token_is_rejected(client):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_protected_route_with_invalid_token_is_rejected(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_protected_route_with_valid_token_returns_current_user(client):
    client.post("/auth/signup", json={"email": "a@example.com", "password": "hunter2"})
    login_response = client.post("/auth/login", json={"email": "a@example.com", "password": "hunter2"})
    token = login_response.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "a@example.com"
