import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import ActionItem, Decision, DiscussionNote, FeedbackCycle, Project, ProjectMembership, Role, User


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


def _user_id(client: TestClient, email: str) -> int:
    with client.session_local() as db:
        return db.query(User).filter(User.email == email).first().id


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


def _add_member(client: TestClient, project_id: int, email: str, role_name: str) -> None:
    with client.session_local() as db:
        user = db.query(User).filter(User.email == email).first()
        role = db.query(Role).filter(Role.name == role_name).first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()


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


def _revealed_cycle(client: TestClient, project_id: int, token: str) -> int:
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "revealed")
    return cycle_id


def _create_cluster(client: TestClient, project_id: int, cycle_id: int, token: str, name: str | None = None) -> int:
    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


def _create_note(client, project_id, cycle_id, cluster_id, token, text="A note"):
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/notes",
        json={"text": text},
        headers={"Authorization": f"Bearer {token}"},
    )


def _list_notes(client, project_id, cycle_id, cluster_id, token):
    return client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/notes",
        headers={"Authorization": f"Bearer {token}"},
    )


def _create_decision(client, project_id, cycle_id, cluster_id, token, description="A decision"):
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/decisions",
        json={"description": description},
        headers={"Authorization": f"Bearer {token}"},
    )


def _list_decisions(client, project_id, cycle_id, cluster_id, token):
    return client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/decisions",
        headers={"Authorization": f"Bearer {token}"},
    )


def _create_action_item(client, project_id, cycle_id, cluster_id, token, **kwargs):
    payload = {"description": kwargs.pop("description", "An action item")}
    payload.update(kwargs)
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/action-items",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _list_action_items(client, project_id, cycle_id, cluster_id, token):
    return client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/action-items",
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# POST/GET .../clusters/{cluster_id}/notes
# ---------------------------------------------------------------------------


def test_create_note_returns_201_with_expected_fields(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    user_id = _user_id(client, "facilitator@example.com")

    response = _create_note(client, project_id, cycle_id, cluster_id, token, text="Team discussed X")

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["cycle_id"] == cycle_id
    assert body["cluster_id"] == cluster_id
    assert body["text"] == "Team discussed X"
    assert body["author_id"] == user_id
    assert "created_at" in body


def test_team_member_can_also_create_note(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")
    member_id = _user_id(client, "member@example.com")

    response = _create_note(client, project_id, cycle_id, cluster_id, member_token, text="Member note")

    assert response.status_code == 201
    assert response.json()["author_id"] == member_id


def test_create_note_with_blank_text_is_422_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _create_note(client, project_id, cycle_id, cluster_id, token, text="   ")

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(DiscussionNote).count() == 0


def test_create_note_on_open_cycle_is_409_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "open")

    response = _create_note(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(DiscussionNote).count() == 0


def test_create_note_on_closed_cycle_is_409_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "closed")

    response = _create_note(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(DiscussionNote).count() == 0


def test_create_note_with_nonexistent_cluster_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _create_note(client, project_id, cycle_id, 999999, token)

    assert response.status_code == 404


def test_create_note_with_cluster_from_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = _create_note(client, project_id, cycle_id, foreign_cluster_id, token)

    assert response.status_code == 404


def test_create_note_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/notes",
        json={"text": "No auth"},
    )

    assert response.status_code == 401


def test_create_note_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _create_note(client, project_id, cycle_id, cluster_id, outsider_token)

    assert response.status_code == 403
    with client.session_local() as db:
        assert db.query(DiscussionNote).count() == 0


def test_create_note_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _create_note(client, project_id, 999999, 1, token)

    assert response.status_code == 404


def test_create_note_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _create_note(client, other_project_id, cycle_id, cluster_id, other_token)

    assert response.status_code == 404


def test_list_notes_returns_entries_ordered_by_created_at_ascending(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    _create_note(client, project_id, cycle_id, cluster_id, token, text="First")
    _create_note(client, project_id, cycle_id, cluster_id, token, text="Second")
    _create_note(client, project_id, cycle_id, cluster_id, token, text="Third")

    response = _list_notes(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 200
    body = response.json()
    assert [n["text"] for n in body] == ["First", "Second", "Third"]
    for note in body:
        assert note["cycle_id"] == cycle_id
        assert note["cluster_id"] == cluster_id


def test_note_created_via_post_appears_in_immediately_subsequent_get(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    create_response = _create_note(client, project_id, cycle_id, cluster_id, token, text="Fresh note")
    note_id = create_response.json()["id"]

    list_response = _list_notes(client, project_id, cycle_id, cluster_id, token)

    assert list_response.status_code == 200
    ids = [n["id"] for n in list_response.json()]
    assert note_id in ids


def test_notes_are_independent_across_two_different_clusters_stateless(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token, name="B")

    # Interleave creation across two clusters in alternating order -- no
    # server-side "current cluster" state exists, so each note must land
    # on the cluster_id named in its own request, regardless of order.
    _create_note(client, project_id, cycle_id, cluster_b, token, text="B first")
    _create_note(client, project_id, cycle_id, cluster_a, token, text="A first")
    _create_note(client, project_id, cycle_id, cluster_a, token, text="A second")
    _create_note(client, project_id, cycle_id, cluster_b, token, text="B second")

    notes_a = _list_notes(client, project_id, cycle_id, cluster_a, token).json()
    notes_b = _list_notes(client, project_id, cycle_id, cluster_b, token).json()

    assert [n["text"] for n in notes_a] == ["A first", "A second"]
    assert [n["text"] for n in notes_b] == ["B first", "B second"]
    for note in notes_a:
        assert note["cluster_id"] == cluster_a
    for note in notes_b:
        assert note["cluster_id"] == cluster_b


def test_list_notes_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "open")

    response = _list_notes(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409


def test_list_notes_on_revealed_cycle_is_200(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _list_notes(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 200


def test_list_notes_on_closed_cycle_still_returns_200_with_entries(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _create_note(client, project_id, cycle_id, cluster_id, token, text="Stays visible")
    _set_cycle_status(client, cycle_id, "closed")

    response = _list_notes(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["text"] == "Stays visible"


def test_list_notes_with_nonexistent_cluster_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _list_notes(client, project_id, cycle_id, 999999, token)

    assert response.status_code == 404


def test_list_notes_with_cluster_from_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = _list_notes(client, project_id, cycle_id, foreign_cluster_id, token)

    assert response.status_code == 404


def test_list_notes_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/notes")

    assert response.status_code == 401


def test_list_notes_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _list_notes(client, project_id, cycle_id, cluster_id, outsider_token)

    assert response.status_code == 403


def test_list_notes_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _list_notes(client, project_id, 999999, 1, token)

    assert response.status_code == 404


def test_list_notes_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _list_notes(client, other_project_id, cycle_id, cluster_id, other_token)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST/GET .../clusters/{cluster_id}/decisions
# ---------------------------------------------------------------------------


def test_create_decision_returns_201_with_expected_fields_and_confirmed_true(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    user_id = _user_id(client, "facilitator@example.com")

    response = _create_decision(client, project_id, cycle_id, cluster_id, token, description="Ship it")

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["cycle_id"] == cycle_id
    assert body["cluster_id"] == cluster_id
    assert body["description"] == "Ship it"
    assert body["author_id"] == user_id
    assert body["confirmed"] is True
    assert "created_at" in body


def test_team_member_can_also_create_decision(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    response = _create_decision(client, project_id, cycle_id, cluster_id, member_token)

    assert response.status_code == 201
    assert response.json()["confirmed"] is True


def test_create_decision_with_blank_description_is_422_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _create_decision(client, project_id, cycle_id, cluster_id, token, description="   ")

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(Decision).count() == 0


def test_create_decision_on_open_cycle_is_409_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "open")

    response = _create_decision(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Decision).count() == 0


def test_create_decision_on_closed_cycle_is_409_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "closed")

    response = _create_decision(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Decision).count() == 0


def test_create_decision_with_nonexistent_cluster_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _create_decision(client, project_id, cycle_id, 999999, token)

    assert response.status_code == 404


def test_create_decision_with_cluster_from_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = _create_decision(client, project_id, cycle_id, foreign_cluster_id, token)

    assert response.status_code == 404


def test_create_decision_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/decisions",
        json={"description": "No auth"},
    )

    assert response.status_code == 401


def test_create_decision_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _create_decision(client, project_id, cycle_id, cluster_id, outsider_token)

    assert response.status_code == 403
    with client.session_local() as db:
        assert db.query(Decision).count() == 0


def test_create_decision_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _create_decision(client, project_id, 999999, 1, token)

    assert response.status_code == 404


def test_create_decision_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _create_decision(client, other_project_id, cycle_id, cluster_id, other_token)

    assert response.status_code == 404


def test_list_decisions_returns_entries_ordered_by_created_at_ascending(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    _create_decision(client, project_id, cycle_id, cluster_id, token, description="First")
    _create_decision(client, project_id, cycle_id, cluster_id, token, description="Second")

    response = _list_decisions(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 200
    body = response.json()
    assert [d["description"] for d in body] == ["First", "Second"]


def test_decision_created_via_post_appears_in_immediately_subsequent_get(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    create_response = _create_decision(client, project_id, cycle_id, cluster_id, token, description="Fresh")
    decision_id = create_response.json()["id"]

    list_response = _list_decisions(client, project_id, cycle_id, cluster_id, token)

    assert list_response.status_code == 200
    ids = [d["id"] for d in list_response.json()]
    assert decision_id in ids


def test_list_decisions_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "open")

    response = _list_decisions(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409


def test_list_decisions_on_closed_cycle_still_returns_200_with_entries(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _create_decision(client, project_id, cycle_id, cluster_id, token, description="Stays visible")
    _set_cycle_status(client, cycle_id, "closed")

    response = _list_decisions(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["description"] == "Stays visible"


def test_list_decisions_with_nonexistent_cluster_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _list_decisions(client, project_id, cycle_id, 999999, token)

    assert response.status_code == 404


def test_list_decisions_with_cluster_from_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = _list_decisions(client, project_id, cycle_id, foreign_cluster_id, token)

    assert response.status_code == 404


def test_list_decisions_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/decisions")

    assert response.status_code == 401


def test_list_decisions_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _list_decisions(client, project_id, cycle_id, cluster_id, outsider_token)

    assert response.status_code == 403


def test_list_decisions_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _list_decisions(client, project_id, 999999, 1, token)

    assert response.status_code == 404


def test_list_decisions_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _list_decisions(client, other_project_id, cycle_id, cluster_id, other_token)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST/GET .../clusters/{cluster_id}/action-items
# ---------------------------------------------------------------------------


def test_create_action_item_returns_201_with_expected_fields(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    user_id = _user_id(client, "facilitator@example.com")

    response = _create_action_item(
        client, project_id, cycle_id, cluster_id, token,
        description="Follow up", due_date="2026-10-01",
    )

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["cycle_id"] == cycle_id
    assert body["cluster_id"] == cluster_id
    assert body["description"] == "Follow up"
    assert body["due_date"] == "2026-10-01"
    assert body["status"] == "open"
    assert body["owner_id"] == user_id
    assert body["confirmed"] is True
    assert "created_at" in body


def test_create_action_item_without_due_date_defaults_to_null(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, token, description="No due date")

    assert response.status_code == 201
    assert response.json()["due_date"] is None


def test_team_member_can_also_create_action_item(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, member_token)

    assert response.status_code == 201


def test_create_action_item_owner_defaults_to_creator_when_omitted(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")
    member_id = _user_id(client, "member@example.com")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, member_token, description="No owner given")

    assert response.status_code == 201
    assert response.json()["owner_id"] == member_id


def test_create_action_item_with_explicit_owner_sets_named_member_not_creator(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    creator_id = _user_id(client, "facilitator@example.com")

    _signup_and_login(client, "bob@example.com")
    _add_member(client, project_id, "bob@example.com", "team_member")
    bob_id = _user_id(client, "bob@example.com")

    response = _create_action_item(
        client, project_id, cycle_id, cluster_id, token,
        description="Bob will do X", owner_id=bob_id,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["owner_id"] == bob_id
    assert body["owner_id"] != creator_id


def test_create_action_item_with_owner_id_for_nonexistent_user_is_404_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _create_action_item(
        client, project_id, cycle_id, cluster_id, token,
        description="Ghost owner", owner_id=999999,
    )

    assert response.status_code == 404
    with client.session_local() as db:
        assert db.query(ActionItem).count() == 0


def test_create_action_item_with_owner_id_for_user_without_membership_is_404_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    _signup_and_login(client, "outsider@example.com")
    outsider_id = _user_id(client, "outsider@example.com")

    response = _create_action_item(
        client, project_id, cycle_id, cluster_id, token,
        description="Non-member owner", owner_id=outsider_id,
    )

    assert response.status_code == 404
    with client.session_local() as db:
        assert db.query(ActionItem).count() == 0


def test_create_action_item_with_blank_description_is_422_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, token, description="   ")

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(ActionItem).count() == 0


def test_create_action_item_with_invalid_due_date_is_422(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _create_action_item(
        client, project_id, cycle_id, cluster_id, token,
        description="Bad date", due_date="not-a-date",
    )

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(ActionItem).count() == 0


def test_create_action_item_status_always_starts_open(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 201
    assert response.json()["status"] == "open"


def test_create_action_item_on_open_cycle_is_409_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "open")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(ActionItem).count() == 0


def test_create_action_item_on_closed_cycle_is_409_and_creates_no_row(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "closed")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(ActionItem).count() == 0


def test_create_action_item_with_nonexistent_cluster_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _create_action_item(client, project_id, cycle_id, 999999, token)

    assert response.status_code == 404


def test_create_action_item_with_cluster_from_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = _create_action_item(client, project_id, cycle_id, foreign_cluster_id, token)

    assert response.status_code == 404


def test_create_action_item_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/action-items",
        json={"description": "No auth"},
    )

    assert response.status_code == 401


def test_create_action_item_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _create_action_item(client, project_id, cycle_id, cluster_id, outsider_token)

    assert response.status_code == 403
    with client.session_local() as db:
        assert db.query(ActionItem).count() == 0


def test_create_action_item_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _create_action_item(client, project_id, 999999, 1, token)

    assert response.status_code == 404


def test_create_action_item_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _create_action_item(client, other_project_id, cycle_id, cluster_id, other_token)

    assert response.status_code == 404


def test_list_action_items_returns_entries_ordered_by_created_at_ascending(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    _create_action_item(client, project_id, cycle_id, cluster_id, token, description="First")
    _create_action_item(client, project_id, cycle_id, cluster_id, token, description="Second")

    response = _list_action_items(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 200
    body = response.json()
    assert [a["description"] for a in body] == ["First", "Second"]


def test_action_item_created_via_post_appears_in_immediately_subsequent_get(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    create_response = _create_action_item(client, project_id, cycle_id, cluster_id, token, description="Fresh")
    action_item_id = create_response.json()["id"]

    list_response = _list_action_items(client, project_id, cycle_id, cluster_id, token)

    assert list_response.status_code == 200
    ids = [a["id"] for a in list_response.json()]
    assert action_item_id in ids


def test_list_action_items_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _set_cycle_status(client, cycle_id, "open")

    response = _list_action_items(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 409


def test_list_action_items_on_closed_cycle_still_returns_200_with_entries(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    _create_action_item(client, project_id, cycle_id, cluster_id, token, description="Stays visible")
    _set_cycle_status(client, cycle_id, "closed")

    response = _list_action_items(client, project_id, cycle_id, cluster_id, token)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["description"] == "Stays visible"


def test_list_action_items_with_nonexistent_cluster_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _list_action_items(client, project_id, cycle_id, 999999, token)

    assert response.status_code == 404


def test_list_action_items_with_cluster_from_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = _list_action_items(client, project_id, cycle_id, foreign_cluster_id, token)

    assert response.status_code == 404


def test_list_action_items_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/action-items")

    assert response.status_code == 401


def test_list_action_items_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _list_action_items(client, project_id, cycle_id, cluster_id, outsider_token)

    assert response.status_code == 403


def test_list_action_items_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _list_action_items(client, project_id, 999999, 1, token)

    assert response.status_code == 404


def test_list_action_items_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Topic")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _list_action_items(client, other_project_id, cycle_id, cluster_id, other_token)

    assert response.status_code == 404
