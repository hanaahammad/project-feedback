import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import FeedbackCycle, Project, ProjectMembership, Role, User


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


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles
# ---------------------------------------------------------------------------


def test_facilitator_can_create_cycle_and_response_has_id_project_id_status(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["project_id"] == project_id
    assert body["status"] == "open"


def test_create_cycle_rejects_team_member_with_403(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "team_member")

    response = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_create_cycle_without_token_is_401(client):
    project_id, _ = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.post(f"/projects/{project_id}/cycles")

    assert response.status_code == 401


def test_create_cycle_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "outsider@example.com", None)

    response = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/cycles/{cycle_id}
# ---------------------------------------------------------------------------


def test_cycle_status_can_be_read_back_after_creation(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    create_response = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    )
    cycle_id = create_response.json()["id"]

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == cycle_id
    assert body["project_id"] == project_id
    assert body["status"] == "open"


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/reveal
# ---------------------------------------------------------------------------


def test_facilitator_can_reveal_open_cycle_and_response_has_id_project_id_status(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == cycle_id
    assert body["project_id"] == project_id
    assert body["status"] == "revealed"


def test_reveal_persists_and_is_reflected_on_subsequent_get(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    assert get_response.json()["status"] == "revealed"


def test_reveal_an_already_revealed_cycle_is_409_and_status_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    first = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert second.status_code == 409

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "revealed"


def test_reveal_a_closed_cycle_is_409_and_status_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        cycle.status = "closed"
        db.commit()

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "closed"


def test_reveal_rejects_team_member_with_403(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    member_token = _signup_and_login(client, "member@example.com")
    with client.session_local() as db:
        user = db.query(User).filter(User.email == "member@example.com").first()
        role = db.query(Role).filter(Role.name == "team_member").first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 403


def test_reveal_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    response = client.post(f"/projects/{project_id}/cycles/{cycle_id}/reveal")

    assert response.status_code == 401


def test_reveal_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


def test_reveal_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.post(
        f"/projects/{project_id}/cycles/999999/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_reveal_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = client.post(
        f"/projects/{other_project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "open"


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/close
# ---------------------------------------------------------------------------


def test_facilitator_can_close_revealed_cycle_and_response_has_id_project_id_status(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == cycle_id
    assert body["project_id"] == project_id
    assert body["status"] == "closed"


def test_close_persists_and_is_reflected_on_subsequent_get(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    assert get_response.json()["status"] == "closed"


def test_close_an_open_cycle_is_409_and_status_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "open"


def test_close_an_already_closed_cycle_is_409_and_status_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )
    first = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert second.status_code == 409

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "closed"


def test_close_cycle_succeeds_even_when_every_topic_is_still_pending(client):
    """Closing is not gated on discussion completeness -- a facilitator can
    close a revealed cycle regardless of how many (if any) clusters have had
    their discussion_status changed away from the pending default.
    """
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": "Still pending"},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "closed"


def test_close_cycle_rejects_team_member_with_403(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    member_token = _signup_and_login(client, "member@example.com")
    with client.session_local() as db:
        user = db.query(User).filter(User.email == "member@example.com").first()
        role = db.query(Role).filter(Role.name == "team_member").first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 403

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "revealed"


def test_close_cycle_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.post(f"/projects/{project_id}/cycles/{cycle_id}/close")

    assert response.status_code == 401


def test_close_cycle_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


def test_close_cycle_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.post(
        f"/projects/{project_id}/cycles/999999/close",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_close_cycle_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["id"]
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = client.post(
        f"/projects/{other_project_id}/cycles/{cycle_id}/close",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404

    get_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.json()["status"] == "revealed"


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/members
# ---------------------------------------------------------------------------


def test_facilitator_can_add_existing_user_as_team_member(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    _signup_and_login(client, "newmember@example.com")

    response = client.post(
        f"/projects/{project_id}/members",
        json={"email": "newmember@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201

    with client.session_local() as db:
        user = db.query(User).filter(User.email == "newmember@example.com").first()
        membership = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.user_id == user.id,
                ProjectMembership.project_id == project_id,
            )
            .first()
        )
        assert membership is not None
        assert membership.role.name == "team_member"

        # No new Role row was created -- still exactly the two seeded roles.
        assert db.query(Role).count() == 2


def test_add_member_who_is_already_a_team_member_is_409_and_creates_no_duplicate(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    _signup_and_login(client, "existing@example.com")

    first_response = client.post(
        f"/projects/{project_id}/members",
        json={"email": "existing@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first_response.status_code == 201

    second_response = client.post(
        f"/projects/{project_id}/members",
        json={"email": "existing@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert second_response.status_code == 409

    with client.session_local() as db:
        user = db.query(User).filter(User.email == "existing@example.com").first()
        count = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.user_id == user.id,
                ProjectMembership.project_id == project_id,
            )
            .count()
        )
        assert count == 1


def test_add_member_who_is_already_a_facilitator_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    # facilitator@example.com already has a facilitator ProjectMembership on this project.

    response = client.post(
        f"/projects/{project_id}/members",
        json={"email": "facilitator@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    with client.session_local() as db:
        user = db.query(User).filter(User.email == "facilitator@example.com").first()
        count = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.user_id == user.id,
                ProjectMembership.project_id == project_id,
            )
            .count()
        )
        assert count == 1


def test_add_member_with_unknown_email_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.post(
        f"/projects/{project_id}/members",
        json={"email": "nobody@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_add_member_rejects_team_member_with_403(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "team_member")
    _signup_and_login(client, "somebody@example.com")

    response = client.post(
        f"/projects/{project_id}/members",
        json={"email": "somebody@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_add_member_without_token_is_401(client):
    project_id, _ = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    _signup_and_login(client, "somebody@example.com")

    response = client.post(
        f"/projects/{project_id}/members",
        json={"email": "somebody@example.com"},
    )

    assert response.status_code == 401
