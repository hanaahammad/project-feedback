import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import FeedbackCard, FeedbackCycle, Project, ProjectMembership, Role, User


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


def _create_cycle(client: TestClient, project_id: int, token: str) -> int:
    response = client.post(
        f"/projects/{project_id}/cycles",
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


def _set_cycle_status(client: TestClient, cycle_id: int, status_value: str) -> None:
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        cycle.status = status_value
        db.commit()


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/cards
# ---------------------------------------------------------------------------


def test_team_member_can_submit_card_and_response_has_id_cycle_id_category_text(client):
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    with client.session_local() as db:
        user = db.query(User).filter(User.email == "member@example.com").first()
        role = db.query(Role).filter(Role.name == "team_member").first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Do more pairing"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["cycle_id"] == cycle_id
    assert body["category"] == "start"
    assert body["text"] == "Do more pairing"


def test_facilitator_can_also_submit_card(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "stop", "text": "Stop skipping standup"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201


def test_member_can_submit_two_cards_in_same_category_and_both_persist(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    first = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "First start card"},
        headers={"Authorization": f"Bearer {token}"},
    )
    second = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Second start card"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    with client.session_local() as db:
        count = (
            db.query(FeedbackCard)
            .filter(FeedbackCard.cycle_id == cycle_id, FeedbackCard.category == "start")
            .count()
        )
        assert count == 2


def test_member_can_submit_cards_under_all_three_categories(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    for category in ("start", "stop", "continue"):
        response = client.post(
            f"/projects/{project_id}/cycles/{cycle_id}/cards",
            json={"category": category, "text": f"A {category} card"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        assert response.json()["category"] == category

    with client.session_local() as db:
        count = db.query(FeedbackCard).filter(FeedbackCard.cycle_id == cycle_id).count()
        assert count == 3


def test_card_is_persisted_with_cycle_id_and_correct_distinct_author_ids(client):
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    with client.session_local() as db:
        member = db.query(User).filter(User.email == "member@example.com").first()
        role = db.query(Role).filter(Role.name == "team_member").first()
        db.add(ProjectMembership(user_id=member.id, project_id=project_id, role_id=role.id))
        db.commit()

    facilitator_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "From facilitator"},
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )
    member_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "From member"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert facilitator_response.status_code == 201
    assert member_response.status_code == 201

    with client.session_local() as db:
        facilitator_user = db.query(User).filter(User.email == "facilitator@example.com").first()
        member_user = db.query(User).filter(User.email == "member@example.com").first()

        facilitator_card = db.query(FeedbackCard).filter(FeedbackCard.id == facilitator_response.json()["id"]).first()
        member_card = db.query(FeedbackCard).filter(FeedbackCard.id == member_response.json()["id"]).first()

        assert facilitator_card.cycle_id == cycle_id
        assert member_card.cycle_id == cycle_id
        assert facilitator_card.author_id == facilitator_user.id
        assert member_card.author_id == member_user.id
        assert facilitator_card.author_id != member_card.author_id


def test_submit_card_with_invalid_category_is_422_and_creates_no_card(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "sideways", "text": "Not a real category"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422

    with client.session_local() as db:
        assert db.query(FeedbackCard).count() == 0


def test_submit_card_with_blank_text_is_422_and_creates_no_card(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422

    with client.session_local() as db:
        assert db.query(FeedbackCard).count() == 0


def test_submit_card_to_revealed_cycle_is_409_and_creates_no_card(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Too late"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    with client.session_local() as db:
        assert db.query(FeedbackCard).count() == 0


def test_submit_card_to_closed_cycle_is_409_and_creates_no_card(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "closed")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Too late"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    with client.session_local() as db:
        assert db.query(FeedbackCard).count() == 0


def test_submit_card_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "No auth"},
    )

    assert response.status_code == 401


def test_submit_card_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "I should not be able to do this"},
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403

    with client.session_local() as db:
        assert db.query(FeedbackCard).count() == 0


def test_submit_card_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = client.post(
        f"/projects/{other_project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Wrong project"},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404

    with client.session_local() as db:
        assert db.query(FeedbackCard).count() == 0


def test_submit_card_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.post(
        f"/projects/{project_id}/cycles/999999/cards",
        json={"category": "start", "text": "No such cycle"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404

    with client.session_local() as db:
        assert db.query(FeedbackCard).count() == 0
