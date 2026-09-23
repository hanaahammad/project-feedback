from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import ActionItem, FeedbackCycle, Project, ProjectMembership, Role, User


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


def _add_member(client: TestClient, project_id: int, email: str, role_name: str) -> None:
    token = _signup_and_login(client, email)
    with client.session_local() as db:
        user = db.query(User).filter(User.email == email).first()
        role = db.query(Role).filter(Role.name == role_name).first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()
    return token


def _create_cycle(client: TestClient, project_id: int) -> int:
    with client.session_local() as db:
        cycle = FeedbackCycle(project_id=project_id, status="revealed")
        db.add(cycle)
        db.commit()
        return cycle.id


def _create_action_item(
    client: TestClient,
    cycle_id: int,
    owner_id: int,
    description: str = "Follow up",
    cluster_id: int | None = None,
    due_date: date | None = None,
    status_value: str = "open",
    confirmed: bool = True,
) -> int:
    with client.session_local() as db:
        item = ActionItem(
            cycle_id=cycle_id,
            cluster_id=cluster_id,
            owner_id=owner_id,
            description=description,
            due_date=due_date,
            status=status_value,
            confirmed=confirmed,
        )
        db.add(item)
        db.commit()
        return item.id


def _list_action_items(client: TestClient, project_id: int, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/projects/{project_id}/action-items", headers=headers)


def _update_status(client: TestClient, project_id: int, action_item_id: int, token: str | None, body: dict):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.patch(
        f"/projects/{project_id}/action-items/{action_item_id}",
        json=body,
        headers=headers,
    )


def _user_id(client: TestClient, email: str) -> int:
    with client.session_local() as db:
        return db.query(User).filter(User.email == email).first().id


def test_list_spans_multiple_cycles_in_project(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    owner_id = _user_id(client, "fac@example.com")
    cycle_a = _create_cycle(client, project_id)
    cycle_b = _create_cycle(client, project_id)
    _create_action_item(client, cycle_a, owner_id, description="From cycle A")
    _create_action_item(client, cycle_b, owner_id, description="From cycle B")

    response = _list_action_items(client, project_id, token)

    assert response.status_code == 200
    descriptions = {row["description"] for row in response.json()}
    assert descriptions == {"From cycle A", "From cycle B"}


def test_empty_project_returns_empty_list(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _list_action_items(client, project_id, token)

    assert response.status_code == 200
    assert response.json() == []


def test_unconfirmed_action_item_excluded_from_list(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    owner_id = _user_id(client, "fac@example.com")
    cycle_id = _create_cycle(client, project_id)
    _create_action_item(client, cycle_id, owner_id, description="Confirmed", confirmed=True)
    _create_action_item(client, cycle_id, owner_id, description="Unconfirmed", confirmed=False)

    response = _list_action_items(client, project_id, token)

    descriptions = {row["description"] for row in response.json()}
    assert descriptions == {"Confirmed"}


def test_list_ordered_by_created_at_ascending(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    owner_id = _user_id(client, "fac@example.com")
    cycle_id = _create_cycle(client, project_id)
    first_id = _create_action_item(client, cycle_id, owner_id, description="First")
    second_id = _create_action_item(client, cycle_id, owner_id, description="Second")

    response = _list_action_items(client, project_id, token)

    ids = [row["id"] for row in response.json()]
    assert ids == [first_id, second_id]


def test_list_includes_cluster_name(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    owner_id = _user_id(client, "fac@example.com")
    cycle_id = _create_cycle(client, project_id)
    with client.session_local() as db:
        from app.models import Cluster

        cluster = Cluster(cycle_id=cycle_id, name="Deploys")
        db.add(cluster)
        db.commit()
        cluster_id = cluster.id
    _create_action_item(client, cycle_id, owner_id, cluster_id=cluster_id)

    response = _list_action_items(client, project_id, token)

    row = response.json()[0]
    assert row["cluster_id"] == cluster_id
    assert row["cluster_name"] == "Deploys"


def test_list_without_auth_is_401(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _list_action_items(client, project_id, None)

    assert response.status_code == 401


def test_list_without_membership_is_403(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _list_action_items(client, project_id, outsider_token)

    assert response.status_code == 403


def test_owner_can_mark_action_item_done(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, owner_id)

    response = _update_status(client, project_id, item_id, token, {"status": "done"})

    assert response.status_code == 200
    assert response.json()["status"] == "done"

    listed = _list_action_items(client, project_id, token).json()
    assert listed[0]["status"] == "done"


def test_status_rejects_invalid_value(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, owner_id)

    response = _update_status(client, project_id, item_id, token, {"status": "in_progress"})

    assert response.status_code == 422
    listed = _list_action_items(client, project_id, token).json()
    assert listed[0]["status"] == "open"


def test_owner_can_reopen_a_done_item(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, owner_id, status_value="done")

    response = _update_status(client, project_id, item_id, token, {"status": "open"})

    assert response.status_code == 200
    assert response.json()["status"] == "open"


def test_non_owner_cannot_update_status_including_facilitator(client: TestClient) -> None:
    project_id, owner_token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    facilitator_token = _add_member(client, project_id, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, owner_id)

    response = _update_status(client, project_id, item_id, facilitator_token, {"status": "done"})

    assert response.status_code == 403
    listed = _list_action_items(client, project_id, owner_token).json()
    assert listed[0]["status"] == "open"


def test_description_owner_and_due_date_not_editable(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(
        client, cycle_id, owner_id, description="Original", due_date=date(2026, 1, 1)
    )

    response = _update_status(
        client,
        project_id,
        item_id,
        token,
        {
            "status": "done",
            "description": "Changed",
            "owner_id": 9999,
            "due_date": str(date.today() + timedelta(days=5)),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "Original"
    assert body["owner_id"] == owner_id
    assert body["due_date"] == "2026-01-01"

    listed = _list_action_items(client, project_id, token).json()[0]
    assert listed["description"] == "Original"
    assert listed["owner_id"] == owner_id
    assert listed["due_date"] == "2026-01-01"


def test_update_nonexistent_action_item_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")

    response = _update_status(client, project_id, 999, token, {"status": "done"})

    assert response.status_code == 404


def test_update_action_item_from_other_project_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    other_project_id, _ = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id)
    item_id = _create_action_item(client, other_cycle_id, owner_id)

    response = _update_status(client, project_id, item_id, token, {"status": "done"})

    assert response.status_code == 404


def test_update_unconfirmed_action_item_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, owner_id, confirmed=False)

    response = _update_status(client, project_id, item_id, token, {"status": "done"})

    assert response.status_code == 404


def test_update_without_auth_is_401(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, owner_id)

    response = _update_status(client, project_id, item_id, None, {"status": "done"})

    assert response.status_code == 401


def test_update_without_membership_is_403(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "owner@example.com", "team_member")
    owner_id = _user_id(client, "owner@example.com")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, owner_id)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _update_status(client, project_id, item_id, outsider_token, {"status": "done"})

    assert response.status_code == 403
