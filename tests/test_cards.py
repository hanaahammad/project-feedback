import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import CardCategory, FeedbackCard, FeedbackCycle, Project, ProjectMembership, Role, User
from app.serializers import serialize_feedback_card


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


# ---------------------------------------------------------------------------
# is_anonymous (#8)
# ---------------------------------------------------------------------------


def test_submit_card_with_is_anonymous_true_is_persisted_anonymous_with_author_id_set(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Anonymous feedback", "is_anonymous": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201

    with client.session_local() as db:
        user = db.query(User).filter(User.email == "facilitator@example.com").first()
        card = db.query(FeedbackCard).filter(FeedbackCard.id == response.json()["id"]).first()
        assert card.is_anonymous is True
        assert card.author_id == user.id


def test_submit_card_with_is_anonymous_omitted_defaults_to_false(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Not anonymous"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == response.json()["id"]).first()
        assert card.is_anonymous is False


def test_submit_card_with_is_anonymous_explicitly_false_is_persisted_not_anonymous(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Explicitly not anonymous", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == response.json()["id"]).first()
        assert card.is_anonymous is False


def test_submission_response_never_includes_an_author_field_for_anonymous_card(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Anonymous feedback", "is_anonymous": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "cycle_id", "category", "text"}
    assert "author_id" not in body


def test_submission_response_never_includes_an_author_field_for_non_anonymous_card(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Not anonymous", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "cycle_id", "category", "text"}
    assert "author_id" not in body


# ---------------------------------------------------------------------------
# serialize_feedback_card (#8) -- exercised directly, not just through HTTP
# ---------------------------------------------------------------------------


def test_serialize_feedback_card_omits_author_id_for_anonymous_card():
    card = FeedbackCard(
        id=1,
        cycle_id=1,
        cluster_id=None,
        category=CardCategory.START,
        text="Anonymous feedback",
        is_anonymous=True,
        author_id=42,
    )

    result = serialize_feedback_card(card)

    assert "author_id" not in result
    assert result["is_anonymous"] is True
    assert result["id"] == 1
    assert result["cycle_id"] == 1
    assert result["category"] == CardCategory.START
    assert result["text"] == "Anonymous feedback"


def test_serialize_feedback_card_includes_author_id_for_non_anonymous_card():
    card = FeedbackCard(
        id=2,
        cycle_id=1,
        cluster_id=None,
        category=CardCategory.STOP,
        text="Not anonymous",
        is_anonymous=False,
        author_id=42,
    )

    result = serialize_feedback_card(card)

    assert result["author_id"] == 42
    assert result["is_anonymous"] is False


def _add_member(client: TestClient, project_id: int, email: str, role_name: str) -> None:
    with client.session_local() as db:
        user = db.query(User).filter(User.email == email).first()
        role = db.query(Role).filter(Role.name == role_name).first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/cycles/{cycle_id}/cards/mine
# ---------------------------------------------------------------------------


def test_list_mine_returns_only_current_users_cards_with_full_serializer_fields(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "My own card", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201

    mine_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/mine",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert mine_response.status_code == 200
    body = mine_response.json()
    assert len(body) == 1
    assert body[0]["text"] == "My own card"
    assert body[0]["category"] == "start"
    assert body[0]["is_anonymous"] is False


def test_list_mine_includes_own_anonymous_card_with_text_and_category_intact(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "stop", "text": "My anonymous card", "is_anonymous": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    mine_response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/mine",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert mine_response.status_code == 200
    body = mine_response.json()
    assert len(body) == 1
    assert body[0]["text"] == "My anonymous card"
    assert body[0]["category"] == "stop"
    assert body[0]["is_anonymous"] is True


def test_list_mine_never_contains_another_users_cards_in_either_direction(client):
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "User A's card"},
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "stop", "text": "User B's card"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    a_mine = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/mine",
        headers={"Authorization": f"Bearer {facilitator_token}"},
    ).json()
    b_mine = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/mine",
        headers={"Authorization": f"Bearer {member_token}"},
    ).json()

    assert [card["text"] for card in a_mine] == ["User A's card"]
    assert [card["text"] for card in b_mine] == ["User B's card"]
    assert "User B's card" not in [card["text"] for card in a_mine]
    assert "User A's card" not in [card["text"] for card in b_mine]


def test_list_mine_is_empty_for_facilitator_with_no_cards_before_reveal(client):
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "continue", "text": "Team member's card"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    facilitator_mine = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/mine",
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )

    assert facilitator_mine.status_code == 200
    assert facilitator_mine.json() == []


def test_list_mine_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.get(
        f"/projects/{project_id}/cycles/999999/cards/mine",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_list_mine_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = client.get(
        f"/projects/{other_project_id}/cycles/{cycle_id}/cards/mine",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


def test_list_mine_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/cards/mine")

    assert response.status_code == 401


def test_list_mine_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/mine",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# PUT /projects/{project_id}/cycles/{cycle_id}/cards/{card_id}
# ---------------------------------------------------------------------------


def test_edit_own_card_succeeds_and_response_reflects_updated_values(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "Updated text", "is_anonymous": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "stop"
    assert body["text"] == "Updated text"
    assert body["is_anonymous"] is True
    assert "author_id" not in body

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.category == CardCategory.STOP
        assert card.text == "Updated text"
        assert card.is_anonymous is True


def test_edit_someone_elses_card_is_404_not_403(client):
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Member's card"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    card_id = create_response.json()["id"]

    # Facilitator tries to edit the team member's card.
    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "Hijacked", "is_anonymous": False},
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )

    assert response.status_code == 404

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.text == "Member's card"
        assert card.category == CardCategory.START


def test_edit_card_in_revealed_cycle_is_409_and_leaves_fields_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]
    _set_cycle_status(client, cycle_id, "revealed")

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "Too late", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.text == "Original text"
        assert card.category == CardCategory.START


def test_edit_card_in_closed_cycle_is_409_and_leaves_fields_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]
    _set_cycle_status(client, cycle_id, "closed")

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "Too late", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.text == "Original text"
        assert card.category == CardCategory.START


def test_edit_card_with_invalid_category_is_422_and_leaves_fields_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "sideways", "text": "Should not apply", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.text == "Original text"
        assert card.category == CardCategory.START


def test_edit_card_with_blank_text_is_422_and_leaves_fields_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "start", "text": "   ", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.text == "Original text"


def test_edit_card_missing_is_anonymous_field_is_422(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "start", "text": "Missing is_anonymous"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


def test_edit_nonexistent_card_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/999999",
        json={"category": "start", "text": "No such card", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_edit_card_id_belonging_to_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    other_cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    response = client.put(
        f"/projects/{project_id}/cycles/{other_cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "Wrong cycle", "is_anonymous": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.text == "Original text"


def test_edit_card_id_belonging_to_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id, other_token)

    response = client.put(
        f"/projects/{other_project_id}/cycles/{other_cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "Wrong project", "is_anonymous": False},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


def test_edit_card_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "No auth", "is_anonymous": False},
    )

    assert response.status_code == 401


def test_edit_card_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    create_response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Original text"},
        headers={"Authorization": f"Bearer {token}"},
    )
    card_id = create_response.json()["id"]

    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.put(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}",
        json={"category": "stop", "text": "Not a member", "is_anonymous": False},
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/cycles/{cycle_id}/cards
# ---------------------------------------------------------------------------


def test_list_all_cards_returns_every_card_in_the_cycle_after_reveal(client):
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Facilitator's card"},
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "stop", "text": "Member's card"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    _set_cycle_status(client, cycle_id, "revealed")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 200
    texts = {card["text"] for card in response.json()}
    assert texts == {"Facilitator's card", "Member's card"}


def test_list_all_cards_includes_cards_from_multiple_different_authors(client):
    # Contrasts with #9's /mine, which is scoped to a single author -- this
    # test proves the all-cards response is not filtered that way.
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    with client.session_local() as db:
        facilitator_user = db.query(User).filter(User.email == "facilitator@example.com").first()
        member_user = db.query(User).filter(User.email == "member@example.com").first()
        facilitator_id = facilitator_user.id
        member_id = member_user.id

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "From facilitator"},
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "stop", "text": "From member"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    _set_cycle_status(client, cycle_id, "revealed")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    author_ids = {card["author_id"] for card in body}
    assert author_ids == {facilitator_id, member_id}


def test_list_all_cards_works_for_closed_cycle_too(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "A card"},
        headers={"Authorization": f"Bearer {token}"},
    )

    _set_cycle_status(client, cycle_id, "closed")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_list_all_cards_facilitator_cannot_see_author_of_anonymous_card_but_can_see_non_anonymous_author(client):
    # This is the key behavioral requirement: reveal must not leak the
    # author of an anonymous card, even to the facilitator who triggered
    # the reveal. Proven end-to-end through the HTTP response, not just at
    # the serializer level.
    project_id, facilitator_token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, facilitator_token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    with client.session_local() as db:
        member_user = db.query(User).filter(User.email == "member@example.com").first()
        member_id = member_user.id

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Anonymous card", "is_anonymous": True},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "stop", "text": "Non-anonymous card", "is_anonymous": False},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    _set_cycle_status(client, cycle_id, "revealed")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {facilitator_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2

    anonymous_card = next(card for card in body if card["text"] == "Anonymous card")
    non_anonymous_card = next(card for card in body if card["text"] == "Non-anonymous card")

    assert anonymous_card["is_anonymous"] is True
    assert "author_id" not in anonymous_card

    assert non_anonymous_card["is_anonymous"] is False
    assert non_anonymous_card["author_id"] == member_id


def test_list_all_cards_for_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": "Still open"},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_list_all_cards_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.get(
        f"/projects/{project_id}/cycles/999999/cards",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_list_all_cards_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = client.get(
        f"/projects/{other_project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


def test_list_all_cards_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/cards")

    assert response.status_code == 401


def test_list_all_cards_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "revealed")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


def test_serialize_feedback_card_hides_author_even_when_called_as_if_by_a_facilitator():
    # The serializer takes no caller/role argument -- the anonymity rule is
    # unconditional, with no facilitator exception. This test documents
    # that by asserting the output is identical regardless of who would be
    # viewing it; there is no "facilitator view" parameter to pass.
    card = FeedbackCard(
        id=3,
        cycle_id=1,
        cluster_id=None,
        category=CardCategory.CONTINUE,
        text="Anonymous feedback for facilitator view",
        is_anonymous=True,
        author_id=99,
    )

    result = serialize_feedback_card(card)

    assert "author_id" not in result
