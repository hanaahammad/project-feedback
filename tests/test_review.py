from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import ActionItem, Decision, FeedbackCycle, Project, ProjectMembership, Role, User


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


def _create_cycle(client: TestClient, project_id: int, ai_summary: str | None = None) -> int:
    with client.session_local() as db:
        cycle = FeedbackCycle(project_id=project_id, status="closed", ai_summary=ai_summary)
        db.add(cycle)
        db.commit()
        return cycle.id


def _create_decision(client: TestClient, cycle_id: int, description: str = "Draft decision", confirmed: bool = False) -> int:
    with client.session_local() as db:
        decision = Decision(cycle_id=cycle_id, cluster_id=None, author_id=None, description=description, confirmed=confirmed)
        db.add(decision)
        db.commit()
        return decision.id


def _create_action_item(
    client: TestClient,
    cycle_id: int,
    description: str = "Draft action",
    confirmed: bool = False,
    owner_id: int | None = None,
) -> int:
    with client.session_local() as db:
        item = ActionItem(
            cycle_id=cycle_id,
            cluster_id=None,
            owner_id=owner_id,
            description=description,
            status="open",
            confirmed=confirmed,
        )
        db.add(item)
        db.commit()
        return item.id


def _headers(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


def _list_drafts(client, project_id, cycle_id, token):
    return client.get(f"/projects/{project_id}/cycles/{cycle_id}/drafts", headers=_headers(token))


def _patch_decision(client, project_id, cycle_id, decision_id, token, body):
    return client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/decisions/{decision_id}", json=body, headers=_headers(token)
    )


def _delete_decision(client, project_id, cycle_id, decision_id, token):
    return client.delete(f"/projects/{project_id}/cycles/{cycle_id}/decisions/{decision_id}", headers=_headers(token))


def _patch_action_item(client, project_id, cycle_id, item_id, token, body):
    return client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/action-items/{item_id}", json=body, headers=_headers(token)
    )


def _delete_action_item(client, project_id, cycle_id, item_id, token):
    return client.delete(f"/projects/{project_id}/cycles/{cycle_id}/action-items/{item_id}", headers=_headers(token))


def _patch_summary(client, project_id, cycle_id, token, body):
    return client.patch(f"/projects/{project_id}/cycles/{cycle_id}/summary", json=body, headers=_headers(token))


# --- list drafts ---


def test_list_drafts_returns_only_unconfirmed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="Summary text")
    _create_decision(client, cycle_id, description="Unconfirmed", confirmed=False)
    _create_decision(client, cycle_id, description="Confirmed", confirmed=True)
    _create_action_item(client, cycle_id, description="Unconfirmed action", confirmed=False)
    _create_action_item(client, cycle_id, description="Confirmed action", confirmed=True)

    response = _list_drafts(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Summary text"
    assert body["summary_confirmed"] is False
    assert [d["description"] for d in body["decisions"]] == ["Unconfirmed"]
    assert [a["description"] for a in body["action_items"]] == ["Unconfirmed action"]


def test_list_drafts_empty_cycle_returns_200_with_empty_lists(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _list_drafts(client, project_id, cycle_id, token)

    assert response.status_code == 200
    assert response.json() == {
        "summary": None,
        "summary_confirmed": False,
        "decisions": [],
        "action_items": [],
    }


# --- decision patch ---


def test_patch_decision_description_only_leaves_confirmed_false(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id, description="Old")

    response = _patch_decision(client, project_id, cycle_id, decision_id, token, {"description": "New"})

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "New"
    assert body["confirmed"] is False


def test_patch_decision_confirmed_only_keeps_text(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id, description="Keep me")

    response = _patch_decision(client, project_id, cycle_id, decision_id, token, {"confirmed": True})

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "Keep me"
    assert body["confirmed"] is True


def test_patch_decision_both_fields_in_one_call(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id, description="Old")

    response = _patch_decision(
        client, project_id, cycle_id, decision_id, token, {"description": "New", "confirmed": True}
    )

    body = response.json()
    assert body["description"] == "New"
    assert body["confirmed"] is True


def test_patch_decision_no_fields_is_noop(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id, description="Unchanged")

    response = _patch_decision(client, project_id, cycle_id, decision_id, token, {})

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "Unchanged"
    assert body["confirmed"] is False


def test_patch_decision_blank_description_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id, description="Original")

    response = _patch_decision(client, project_id, cycle_id, decision_id, token, {"description": "   "})

    assert response.status_code == 422
    with client.session_local() as db:
        decision = db.query(Decision).filter(Decision.id == decision_id).first()
        assert decision.description == "Original"


def test_patch_confirmed_decision_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id, description="Locked", confirmed=True)

    response = _patch_decision(client, project_id, cycle_id, decision_id, token, {"description": "Changed"})

    assert response.status_code == 409
    with client.session_local() as db:
        decision = db.query(Decision).filter(Decision.id == decision_id).first()
        assert decision.description == "Locked"


def test_delete_unconfirmed_decision(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id)

    response = _delete_decision(client, project_id, cycle_id, decision_id, token)

    assert response.status_code == 204
    with client.session_local() as db:
        assert db.query(Decision).filter(Decision.id == decision_id).first() is None

    drafts = _list_drafts(client, project_id, cycle_id, token).json()
    assert drafts["decisions"] == []


def test_delete_confirmed_decision_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id, confirmed=True)

    response = _delete_decision(client, project_id, cycle_id, decision_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Decision).filter(Decision.id == decision_id).first() is not None


# --- action item patch ---


def test_patch_action_item_all_fields(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _add_member(client, project_id, "bob@example.com", "team_member")
    with client.session_local() as db:
        bob_id = db.query(User).filter(User.email == "bob@example.com").first().id
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, description="Old")

    response = _patch_action_item(
        client,
        project_id,
        cycle_id,
        item_id,
        token,
        {
            "description": "New",
            "due_date": "2026-11-01",
            "owner_id": bob_id,
            "confirmed": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "New"
    assert body["due_date"] == "2026-11-01"
    assert body["owner_id"] == bob_id
    assert body["confirmed"] is True


def test_patch_action_item_no_fields_is_noop(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, description="Unchanged")

    response = _patch_action_item(client, project_id, cycle_id, item_id, token, {})

    assert response.status_code == 200
    assert response.json()["description"] == "Unchanged"
    assert response.json()["confirmed"] is False


def test_patch_action_item_blank_description_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, description="Original")

    response = _patch_action_item(client, project_id, cycle_id, item_id, token, {"description": ""})

    assert response.status_code == 422
    with client.session_local() as db:
        item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
        assert item.description == "Original"


def test_patch_action_item_invalid_owner_id_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id)

    response = _patch_action_item(client, project_id, cycle_id, item_id, token, {"owner_id": 99999})

    assert response.status_code == 404
    with client.session_local() as db:
        item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
        assert item.owner_id is None


def test_patch_action_item_owner_id_for_user_with_no_membership_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _signup_and_login(client, "outsider@example.com")
    with client.session_local() as db:
        outsider_id = db.query(User).filter(User.email == "outsider@example.com").first().id
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id)

    response = _patch_action_item(client, project_id, cycle_id, item_id, token, {"owner_id": outsider_id})

    assert response.status_code == 404


def test_patch_confirmed_action_item_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, description="Locked", confirmed=True)

    response = _patch_action_item(client, project_id, cycle_id, item_id, token, {"description": "Changed"})

    assert response.status_code == 409
    with client.session_local() as db:
        item = db.query(ActionItem).filter(ActionItem.id == item_id).first()
        assert item.description == "Locked"


def test_delete_unconfirmed_action_item(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id)

    response = _delete_action_item(client, project_id, cycle_id, item_id, token)

    assert response.status_code == 204
    with client.session_local() as db:
        assert db.query(ActionItem).filter(ActionItem.id == item_id).first() is None


def test_delete_confirmed_action_item_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, confirmed=True)

    response = _delete_action_item(client, project_id, cycle_id, item_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(ActionItem).filter(ActionItem.id == item_id).first() is not None


# --- confirming feeds #17 ---


def test_confirming_decision_removes_it_from_drafts(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id)

    _patch_decision(client, project_id, cycle_id, decision_id, token, {"confirmed": True})

    drafts = _list_drafts(client, project_id, cycle_id, token).json()
    assert drafts["decisions"] == []


def test_confirming_action_item_appears_in_project_action_items_list(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, cycle_id, description="Now confirmed")

    _patch_action_item(client, project_id, cycle_id, item_id, token, {"confirmed": True})

    drafts = _list_drafts(client, project_id, cycle_id, token).json()
    assert drafts["action_items"] == []

    listing = client.get(
        f"/projects/{project_id}/action-items", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert any(row["description"] == "Now confirmed" for row in listing)


# --- summary patch ---


def test_patch_summary_text_only_leaves_unconfirmed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="Old summary")

    response = _patch_summary(client, project_id, cycle_id, token, {"summary": "New summary"})

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "New summary"
    assert body["summary_confirmed"] is False


def test_patch_summary_confirmed_only_keeps_text(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="Keep me")

    response = _patch_summary(client, project_id, cycle_id, token, {"confirmed": True})

    body = response.json()
    assert body["summary"] == "Keep me"
    assert body["summary_confirmed"] is True


def test_patch_confirmed_summary_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="Locked")
    _patch_summary(client, project_id, cycle_id, token, {"confirmed": True})

    response = _patch_summary(client, project_id, cycle_id, token, {"summary": "Changed"})

    assert response.status_code == 409
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.ai_summary == "Locked"


# --- auth/permissions/404s, parametrized across the six endpoints ---


def _all_endpoint_calls(project_id, cycle_id, decision_id, item_id, token):
    return [
        lambda c: _list_drafts(c, project_id, cycle_id, token),
        lambda c: _patch_decision(c, project_id, cycle_id, decision_id, token, {"confirmed": True}),
        lambda c: _delete_decision(c, project_id, cycle_id, decision_id, token),
        lambda c: _patch_action_item(c, project_id, cycle_id, item_id, token, {"confirmed": True}),
        lambda c: _delete_action_item(c, project_id, cycle_id, item_id, token),
        lambda c: _patch_summary(c, project_id, cycle_id, token, {"confirmed": True}),
    ]


def test_all_six_endpoints_reject_team_member_with_403(client: TestClient) -> None:
    project_id, fac_token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    member_token = _add_member(client, project_id, "member@example.com", "team_member")

    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id)
    item_id = _create_action_item(client, cycle_id)

    for call in _all_endpoint_calls(project_id, cycle_id, decision_id, item_id, member_token):
        response = call(client)
        assert response.status_code == 403


def test_all_six_endpoints_reject_missing_auth_with_401(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id)
    item_id = _create_action_item(client, cycle_id)

    for call in _all_endpoint_calls(project_id, cycle_id, decision_id, item_id, None):
        response = call(client)
        assert response.status_code == 401


def test_all_six_endpoints_reject_non_member_with_403(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, cycle_id)
    item_id = _create_action_item(client, cycle_id)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    for call in _all_endpoint_calls(project_id, cycle_id, decision_id, item_id, outsider_token):
        response = call(client)
        assert response.status_code == 403


def test_all_six_endpoints_reject_missing_cycle_with_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    for call in _all_endpoint_calls(project_id, 999, 1, 1, token):
        response = call(client)
        assert response.status_code == 404


def test_all_six_endpoints_reject_cycle_from_other_project_with_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    other_project_id, _ = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id)
    other_decision_id = _create_decision(client, other_cycle_id)
    other_item_id = _create_action_item(client, other_cycle_id)

    for call in _all_endpoint_calls(project_id, other_cycle_id, other_decision_id, other_item_id, token):
        response = call(client)
        assert response.status_code == 404


def test_patch_decision_missing_decision_id_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _patch_decision(client, project_id, cycle_id, 999, token, {"confirmed": True})

    assert response.status_code == 404


def test_patch_decision_from_other_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    other_cycle_id = _create_cycle(client, project_id)
    decision_id = _create_decision(client, other_cycle_id)

    response = _patch_decision(client, project_id, cycle_id, decision_id, token, {"confirmed": True})

    assert response.status_code == 404


def test_patch_action_item_missing_id_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _patch_action_item(client, project_id, cycle_id, 999, token, {"confirmed": True})

    assert response.status_code == 404


def test_patch_action_item_from_other_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    other_cycle_id = _create_cycle(client, project_id)
    item_id = _create_action_item(client, other_cycle_id)

    response = _patch_action_item(client, project_id, cycle_id, item_id, token, {"confirmed": True})

    assert response.status_code == 404
