import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import ActionItem, Cluster, FeedbackCard, FeedbackCycle, Project, ProjectMembership, Role, User


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

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


def _add_member(client: TestClient, project_id: int, email: str, role_name: str) -> str:
    token = _signup_and_login(client, email)
    with client.session_local() as db:
        user = db.query(User).filter(User.email == email).first()
        role = db.query(Role).filter(Role.name == role_name).first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()
    return token


def _user_id(client: TestClient, email: str) -> int:
    with client.session_local() as db:
        return db.query(User).filter(User.email == email).first().id


def _create_cycle(client: TestClient, project_id: int, status_value: str = "open") -> int:
    with client.session_local() as db:
        cycle = FeedbackCycle(project_id=project_id, status=status_value)
        db.add(cycle)
        db.commit()
        return cycle.id


def _get_dashboard(client: TestClient, project_id: int, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/projects/{project_id}/dashboard", headers=headers)


def test_no_cycles_returns_null_current_and_empty_lists(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _get_dashboard(client, project_id, token)

    assert response.status_code == 200
    assert response.json() == {"current_cycle": None, "previous_cycles": [], "open_action_items": []}


def test_current_cycle_is_most_recently_created_non_closed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    older_id = _create_cycle(client, project_id, status_value="revealed")
    newer_id = _create_cycle(client, project_id, status_value="open")

    response = _get_dashboard(client, project_id, token)

    assert response.json()["current_cycle"]["id"] == newer_id
    assert response.json()["current_cycle"]["id"] != older_id


def test_current_cycle_null_when_all_cycles_closed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _create_cycle(client, project_id, status_value="closed")

    response = _get_dashboard(client, project_id, token)

    assert response.json()["current_cycle"] is None


def test_submission_count_counts_all_cards_regardless_of_author_or_anonymity(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _add_member(client, project_id, "a@example.com", "team_member")
    _add_member(client, project_id, "b@example.com", "team_member")
    a_id, b_id = _user_id(client, "a@example.com"), _user_id(client, "b@example.com")
    cycle_id = _create_cycle(client, project_id, status_value="open")

    with client.session_local() as db:
        db.add(FeedbackCard(cycle_id=cycle_id, author_id=a_id, category="start", text="1"))
        db.add(FeedbackCard(cycle_id=cycle_id, author_id=a_id, category="stop", text="2", is_anonymous=True))
        db.add(FeedbackCard(cycle_id=cycle_id, author_id=b_id, category="continue", text="3"))
        db.commit()

    response = _get_dashboard(client, project_id, token)

    assert response.json()["current_cycle"]["submission_count"] == 3


def test_previous_cycles_ordered_descending_by_created_at(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    first_closed = _create_cycle(client, project_id, status_value="closed")
    second_closed = _create_cycle(client, project_id, status_value="closed")

    response = _get_dashboard(client, project_id, token)

    ids = [c["id"] for c in response.json()["previous_cycles"]]
    assert ids == [second_closed, first_closed]
    assert all(c["status"] == "closed" for c in response.json()["previous_cycles"])


def test_previous_cycles_empty_when_none_closed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _create_cycle(client, project_id, status_value="open")

    response = _get_dashboard(client, project_id, token)

    assert response.json()["previous_cycles"] == []


def test_open_action_items_span_every_cycle_including_closed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    closed_cycle_id = _create_cycle(client, project_id, status_value="closed")

    with client.session_local() as db:
        db.add(
            ActionItem(
                cycle_id=closed_cycle_id, description="From closed cycle", status="open", confirmed=True
            )
        )
        db.commit()

    response = _get_dashboard(client, project_id, token)

    descriptions = [item["description"] for item in response.json()["open_action_items"]]
    assert "From closed cycle" in descriptions


def test_open_action_items_excludes_done(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="open")

    with client.session_local() as db:
        db.add(ActionItem(cycle_id=cycle_id, description="Still open", status="open", confirmed=True))
        db.add(ActionItem(cycle_id=cycle_id, description="Already done", status="done", confirmed=True))
        db.commit()

    response = _get_dashboard(client, project_id, token)

    descriptions = {item["description"] for item in response.json()["open_action_items"]}
    assert descriptions == {"Still open"}


def test_open_action_items_excludes_unconfirmed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="open")

    with client.session_local() as db:
        db.add(ActionItem(cycle_id=cycle_id, description="Confirmed", status="open", confirmed=True))
        db.add(ActionItem(cycle_id=cycle_id, description="Draft", status="open", confirmed=False))
        db.commit()

    response = _get_dashboard(client, project_id, token)

    descriptions = {item["description"] for item in response.json()["open_action_items"]}
    assert descriptions == {"Confirmed"}


def test_open_action_items_include_cluster_name(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="open")

    with client.session_local() as db:
        cluster = Cluster(cycle_id=cycle_id, name="Deploys")
        db.add(cluster)
        db.flush()
        db.add(
            ActionItem(
                cycle_id=cycle_id, cluster_id=cluster.id, description="Fix pipeline", status="open", confirmed=True
            )
        )
        db.commit()

    response = _get_dashboard(client, project_id, token)

    item = response.json()["open_action_items"][0]
    assert item["cluster_name"] == "Deploys"


def test_team_member_can_call_endpoint(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    member_token = _add_member(client, project_id, "member@example.com", "team_member")

    response = _get_dashboard(client, project_id, member_token)

    assert response.status_code == 200


def test_endpoint_is_read_only_and_idempotent(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="open")
    with client.session_local() as db:
        db.add(FeedbackCard(cycle_id=cycle_id, author_id=_user_id(client, "fac@example.com"), category="start", text="x"))
        db.add(ActionItem(cycle_id=cycle_id, description="A", status="open", confirmed=True))
        db.commit()

    def counts():
        with client.session_local() as db:
            return (
                db.query(FeedbackCycle).count(),
                db.query(FeedbackCard).count(),
                db.query(ActionItem).count(),
            )

    before = counts()
    first = _get_dashboard(client, project_id, token)
    second = _get_dashboard(client, project_id, token)
    after = counts()

    assert first.json() == second.json()
    assert before == after


def test_dashboard_without_auth_is_401(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _get_dashboard(client, project_id, None)

    assert response.status_code == 401


def test_dashboard_without_membership_is_403(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _get_dashboard(client, project_id, outsider_token)

    assert response.status_code == 403


def test_dashboard_for_nonexistent_project_is_403(client: TestClient) -> None:
    token = _signup_and_login(client, "someone@example.com")

    response = _get_dashboard(client, 999999, token)

    assert response.status_code == 403
