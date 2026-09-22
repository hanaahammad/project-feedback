import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Project, ProjectMembership, Role, User


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
    # migration does (verified separately against a real `alembic upgrade head`).
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


def _make_project_with_membership(client: TestClient, email: str, role_name: str | None) -> tuple[int, str]:
    """Signs up/logs in a user, creates a project, and (unless role_name is None)
    gives that user a ProjectMembership with the given role on that project.
    Returns (project_id, access_token).
    """
    token = _signup_and_login(client, email)
    with client.session_local() as db:
        user = db.query(User).filter(User.email == email).first()
        project = Project(name="Test Project")
        db.add(project)
        db.flush()

        if role_name is not None:
            role = db.query(Role).filter(Role.name == role_name).first()
            db.add(ProjectMembership(user_id=user.id, project_id=project.id, role_id=role.id))

        db.commit()
        project_id = project.id

    return project_id, token


def test_facilitator_ping_rejects_team_member_with_403(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "team_member")

    response = client.get(
        f"/projects/{project_id}/facilitator-ping",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_facilitator_ping_allows_facilitator_with_200(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.get(
        f"/projects/{project_id}/facilitator-ping",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_facilitator_ping_without_token_is_401(client):
    with client.session_local() as db:
        project = Project(name="Test Project")
        db.add(project)
        db.commit()
        project_id = project.id

    response = client.get(f"/projects/{project_id}/facilitator-ping")
    assert response.status_code == 401


def test_facilitator_ping_with_invalid_token_is_401(client):
    with client.session_local() as db:
        project = Project(name="Test Project")
        db.add(project)
        db.commit()
        project_id = project.id

    response = client.get(
        f"/projects/{project_id}/facilitator-ping",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_facilitator_ping_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "outsider@example.com", None)

    response = client.get(
        f"/projects/{project_id}/facilitator-ping",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
