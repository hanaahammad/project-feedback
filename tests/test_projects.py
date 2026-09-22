import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import ProjectMembership, Role, User


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Base.metadata.create_all() builds the schema but doesn't run migration
    # data seeds, so seed the two roles here the same way the real Alembic
    # migration does (see tests/test_roles.py).
    with TestingSessionLocal() as db:
        db.add_all([Role(name="team_member"), Role(name="facilitator")])
        db.commit()

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


def _signup_and_login(client: TestClient, email: str) -> str:
    client.post("/auth/signup", json={"email": email, "password": "hunter2"})
    response = client.post("/auth/login", json={"email": email, "password": "hunter2"})
    return response.json()["access_token"]


def test_create_project_returns_id_and_submitted_fields(client):
    token = _signup_and_login(client, "creator@example.com")

    response = client.post(
        "/projects",
        json={
            "name": "Sprint Retro",
            "description": "Weekly retro",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["name"] == "Sprint Retro"
    assert body["description"] == "Weekly retro"
    assert body["start_date"] == "2026-01-01"
    assert body["end_date"] == "2026-01-31"


def test_create_project_with_only_name_succeeds(client):
    token = _signup_and_login(client, "creator@example.com")

    response = client.post(
        "/projects",
        json={"name": "Minimal Project"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Minimal Project"
    assert body["description"] is None
    assert body["start_date"] is None
    assert body["end_date"] is None


def test_create_project_without_name_is_rejected_and_creates_no_row(client):
    token = _signup_and_login(client, "creator@example.com")

    response = client.post(
        "/projects",
        json={"description": "No name here"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    with client.session_local() as db:
        from app.models import Project

        assert db.query(Project).count() == 0


def test_create_project_with_blank_name_is_rejected_and_creates_no_row(client):
    token = _signup_and_login(client, "creator@example.com")

    response = client.post(
        "/projects",
        json={"name": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    with client.session_local() as db:
        from app.models import Project

        assert db.query(Project).count() == 0


def test_create_project_without_token_is_401(client):
    response = client.post("/projects", json={"name": "No Auth"})
    assert response.status_code == 401


def test_create_project_records_creator_as_facilitator(client):
    token = _signup_and_login(client, "creator@example.com")

    response = client.post(
        "/projects",
        json={"name": "Facilitator Test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    project_id = response.json()["id"]

    with client.session_local() as db:
        user = db.query(User).filter(User.email == "creator@example.com").first()
        membership = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.user_id == user.id,
                ProjectMembership.project_id == project_id,
            )
            .first()
        )
        assert membership is not None
        assert membership.role.name == "facilitator"


def test_get_project_returns_submitted_fields(client):
    token = _signup_and_login(client, "creator@example.com")
    create_response = client.post(
        "/projects",
        json={
            "name": "Viewable Project",
            "description": "desc",
            "start_date": "2026-02-01",
            "end_date": "2026-02-28",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    project_id = create_response.json()["id"]

    response = client.get(f"/projects/{project_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == project_id
    assert body["name"] == "Viewable Project"
    assert body["description"] == "desc"
    assert body["start_date"] == "2026-02-01"
    assert body["end_date"] == "2026-02-28"


def test_get_nonexistent_project_returns_404(client):
    token = _signup_and_login(client, "creator@example.com")

    response = client.get("/projects/999999", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 404


def test_get_project_without_token_is_401(client):
    token = _signup_and_login(client, "creator@example.com")
    create_response = client.post(
        "/projects",
        json={"name": "Some Project"},
        headers={"Authorization": f"Bearer {token}"},
    )
    project_id = create_response.json()["id"]

    response = client.get(f"/projects/{project_id}")

    assert response.status_code == 401


def test_get_project_does_not_require_membership(client):
    creator_token = _signup_and_login(client, "creator@example.com")
    create_response = client.post(
        "/projects",
        json={"name": "Non-member viewable"},
        headers={"Authorization": f"Bearer {creator_token}"},
    )
    project_id = create_response.json()["id"]

    other_token = _signup_and_login(client, "other@example.com")
    response = client.get(f"/projects/{project_id}", headers={"Authorization": f"Bearer {other_token}"})

    assert response.status_code == 200
    assert response.json()["name"] == "Non-member viewable"
