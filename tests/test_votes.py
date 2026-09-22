import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import FeedbackCycle, Project, ProjectMembership, Role, User, Vote


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


def _cast_votes(client: TestClient, project_id: int, cycle_id: int, token: str, cluster_ids: list[int]):
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/votes",
        json={"cluster_ids": cluster_ids},
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_my_votes(client: TestClient, project_id: int, cycle_id: int, token: str):
    return client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/votes/mine",
        headers={"Authorization": f"Bearer {token}"},
    )


def _close_voting(client: TestClient, project_id: int, cycle_id: int, token: str):
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/close-voting",
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_vote_results(client: TestClient, project_id: int, cycle_id: int, token: str):
    return client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/votes/results",
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/votes
# ---------------------------------------------------------------------------


def test_cast_votes_succeeds_with_200_and_response_reflects_submitted_multiset(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token, name="B")

    response = _cast_votes(client, project_id, cycle_id, token, [cluster_a, cluster_a, cluster_b])

    assert response.status_code == 200
    body = response.json()
    assert body["cycle_id"] == cycle_id
    assert sorted(body["cluster_ids"]) == sorted([cluster_a, cluster_a, cluster_b])

    with client.session_local() as db:
        votes = db.query(Vote).all()
        assert len(votes) == 3


def test_cast_votes_with_more_than_three_entries_is_422_and_no_votes_created(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")

    response = _cast_votes(client, project_id, cycle_id, token, [cluster_a, cluster_a, cluster_a, cluster_a])

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(Vote).count() == 0


def test_cast_votes_with_empty_list_succeeds_and_clears_existing_votes(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")

    _cast_votes(client, project_id, cycle_id, token, [cluster_a, cluster_a])

    response = _cast_votes(client, project_id, cycle_id, token, [])

    assert response.status_code == 200
    body = response.json()
    assert body == {"cycle_id": cycle_id, "cluster_ids": []}
    with client.session_local() as db:
        assert db.query(Vote).count() == 0


def test_cast_empty_votes_without_prior_submission_still_succeeds(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _cast_votes(client, project_id, cycle_id, token, [])

    assert response.status_code == 200
    assert response.json() == {"cycle_id": cycle_id, "cluster_ids": []}


def test_cast_votes_with_invalid_cluster_id_is_404_and_no_partial_writes(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")

    response = _cast_votes(client, project_id, cycle_id, token, [cluster_a, 999999])

    assert response.status_code == 404
    with client.session_local() as db:
        # Neither the valid nor the invalid id resulted in a written row --
        # the whole submission is validated before anything is committed.
        assert db.query(Vote).count() == 0


def test_cast_votes_with_cluster_id_from_a_different_cycle_is_404_and_no_partial_writes(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")

    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = _cast_votes(client, project_id, cycle_id, token, [cluster_a, foreign_cluster])

    assert response.status_code == 404
    with client.session_local() as db:
        assert db.query(Vote).count() == 0


def test_cast_votes_on_open_cycle_is_409_and_no_votes_created(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = _cast_votes(client, project_id, cycle_id, token, [])

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Vote).count() == 0


def test_cast_votes_on_closed_cycle_is_409_and_no_votes_changed(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    _set_cycle_status(client, cycle_id, "closed")

    response = _cast_votes(client, project_id, cycle_id, token, [cluster_a])

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Vote).count() == 0


def test_resubmission_atomically_replaces_prior_votes(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token, name="B")
    cluster_c = _create_cluster(client, project_id, cycle_id, token, name="C")

    first = _cast_votes(client, project_id, cycle_id, token, [cluster_a, cluster_a, cluster_b])
    assert first.status_code == 200

    second = _cast_votes(client, project_id, cycle_id, token, [cluster_c])
    assert second.status_code == 200
    assert second.json()["cluster_ids"] == [cluster_c]

    with client.session_local() as db:
        votes = db.query(Vote).all()
        assert len(votes) == 1
        assert votes[0].cluster_id == cluster_c


def test_duplicate_cluster_ids_in_one_submission_persist_as_separate_rows(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token, name="B")

    response = _cast_votes(client, project_id, cycle_id, token, [cluster_a, cluster_a, cluster_b])

    assert response.status_code == 200
    with client.session_local() as db:
        votes = db.query(Vote).all()
        assert len(votes) == 3
        cluster_ids = sorted(v.cluster_id for v in votes)
        assert cluster_ids == sorted([cluster_a, cluster_a, cluster_b])


def test_votes_are_isolated_per_participant(client):
    project_id, token_x = _make_project_with_membership(client, "x@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token_x)
    cluster_a = _create_cluster(client, project_id, cycle_id, token_x, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token_x, name="B")

    token_y = _signup_and_login(client, "y@example.com")
    _add_member(client, project_id, "y@example.com", "team_member")

    response_x = _cast_votes(client, project_id, cycle_id, token_x, [cluster_a, cluster_a, cluster_a])
    response_y = _cast_votes(client, project_id, cycle_id, token_y, [cluster_b])

    assert response_x.status_code == 200
    assert response_y.status_code == 200
    assert sorted(response_x.json()["cluster_ids"]) == sorted([cluster_a, cluster_a, cluster_a])
    assert response_y.json()["cluster_ids"] == [cluster_b]

    with client.session_local() as db:
        votes = db.query(Vote).all()
        assert len(votes) == 4
        with_user_x = db.query(User).filter(User.email == "x@example.com").first()
        with_user_y = db.query(User).filter(User.email == "y@example.com").first()
        x_votes = [v for v in votes if v.participant_id == with_user_x.id]
        y_votes = [v for v in votes if v.participant_id == with_user_y.id]
        assert len(x_votes) == 3
        assert all(v.cluster_id == cluster_a for v in x_votes)
        assert len(y_votes) == 1
        assert y_votes[0].cluster_id == cluster_b


def test_cast_votes_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/votes",
        json={"cluster_ids": []},
    )

    assert response.status_code == 401


def test_cast_votes_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _cast_votes(client, project_id, cycle_id, outsider_token, [])

    assert response.status_code == 403


def test_cast_votes_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")

    response = _cast_votes(client, project_id, 999999, token, [])

    assert response.status_code == 404


def test_cast_votes_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "team_member")

    response = _cast_votes(client, other_project_id, cycle_id, other_token, [])

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/cycles/{cycle_id}/votes/mine
# ---------------------------------------------------------------------------


def test_get_my_votes_returns_current_allocation(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token, name="B")
    _cast_votes(client, project_id, cycle_id, token, [cluster_a, cluster_a, cluster_b])

    response = _get_my_votes(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body["cycle_id"] == cycle_id
    assert sorted(body["cluster_ids"]) == sorted([cluster_a, cluster_a, cluster_b])


def test_get_my_votes_empty_state(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _get_my_votes(client, project_id, cycle_id, token)

    assert response.status_code == 200
    assert response.json() == {"cycle_id": cycle_id, "cluster_ids": []}


def test_get_my_votes_does_not_include_another_participants_votes(client):
    project_id, token_x = _make_project_with_membership(client, "x@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token_x)
    cluster_a = _create_cluster(client, project_id, cycle_id, token_x, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token_x, name="B")

    token_y = _signup_and_login(client, "y@example.com")
    _add_member(client, project_id, "y@example.com", "team_member")

    _cast_votes(client, project_id, cycle_id, token_x, [cluster_a])
    _cast_votes(client, project_id, cycle_id, token_y, [cluster_b])

    response_x = _get_my_votes(client, project_id, cycle_id, token_x)
    response_y = _get_my_votes(client, project_id, cycle_id, token_y)

    assert response_x.json()["cluster_ids"] == [cluster_a]
    assert response_y.json()["cluster_ids"] == [cluster_b]


def test_get_my_votes_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = _get_my_votes(client, project_id, cycle_id, token)

    assert response.status_code == 409


def test_get_my_votes_on_revealed_cycle_is_200(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _get_my_votes(client, project_id, cycle_id, token)

    assert response.status_code == 200


def test_get_my_votes_on_closed_cycle_is_200(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    _cast_votes(client, project_id, cycle_id, token, [cluster_a])
    _set_cycle_status(client, cycle_id, "closed")

    response = _get_my_votes(client, project_id, cycle_id, token)

    assert response.status_code == 200
    assert response.json()["cluster_ids"] == [cluster_a]


def test_get_my_votes_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/votes/mine")

    assert response.status_code == 401


def test_get_my_votes_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _get_my_votes(client, project_id, cycle_id, outsider_token)

    assert response.status_code == 403


def test_get_my_votes_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")

    response = _get_my_votes(client, project_id, 999999, token)

    assert response.status_code == 404


def test_get_my_votes_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "member@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "team_member")

    response = _get_my_votes(client, other_project_id, cycle_id, other_token)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/close-voting
# ---------------------------------------------------------------------------


def test_close_voting_succeeds_as_facilitator_even_if_not_everyone_voted(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    token_y = _signup_and_login(client, "y@example.com")
    _add_member(client, project_id, "y@example.com", "team_member")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    _cast_votes(client, project_id, cycle_id, token, [cluster_a])
    # y never votes

    response = _close_voting(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body == {"cycle_id": cycle_id, "project_id": project_id, "voting_closed": True}
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.voting_closed is True


def test_close_voting_from_team_member_is_403(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    token_member = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _close_voting(client, project_id, cycle_id, token_member)

    assert response.status_code == 403
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.voting_closed is False


def test_close_voting_on_open_cycle_is_409_and_voting_closed_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = _close_voting(client, project_id, cycle_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.voting_closed is False


def test_close_voting_on_closed_cycle_is_409_and_voting_closed_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "closed")

    response = _close_voting(client, project_id, cycle_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.voting_closed is False


def test_close_voting_called_twice_is_409_on_second_call_and_stays_closed(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    first = _close_voting(client, project_id, cycle_id, token)
    second = _close_voting(client, project_id, cycle_id, token)

    assert first.status_code == 200
    assert second.status_code == 409
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.voting_closed is True


def test_close_voting_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.post(f"/projects/{project_id}/cycles/{cycle_id}/close-voting")

    assert response.status_code == 401


def test_close_voting_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _close_voting(client, project_id, cycle_id, outsider_token)

    assert response.status_code == 403


def test_close_voting_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _close_voting(client, project_id, 999999, token)

    assert response.status_code == 404


def test_close_voting_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _close_voting(client, other_project_id, cycle_id, other_token)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/cycles/{cycle_id}/votes/results
# ---------------------------------------------------------------------------


def test_vote_results_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = _get_vote_results(client, project_id, cycle_id, token)

    assert response.status_code == 409


def test_vote_results_unavailable_before_everyone_voted_and_before_close(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    token_y = _signup_and_login(client, "y@example.com")
    _add_member(client, project_id, "y@example.com", "team_member")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    _cast_votes(client, project_id, cycle_id, token, [cluster_a])
    # y has not voted yet

    response = _get_vote_results(client, project_id, cycle_id, token)

    assert response.status_code == 409
    assert "cluster" not in response.json()
    assert response.json() == {"detail": "voting results are not available yet"}


def test_vote_results_becomes_available_once_everyone_voted_with_no_close_action(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    token_y = _signup_and_login(client, "y@example.com")
    _add_member(client, project_id, "y@example.com", "team_member")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    _cast_votes(client, project_id, cycle_id, token, [cluster_a])
    _cast_votes(client, project_id, cycle_id, token_y, [cluster_a])

    response = _get_vote_results(client, project_id, cycle_id, token)

    assert response.status_code == 200
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        # No explicit close action was taken -- availability is computed live.
        assert cycle.voting_closed is False


def test_vote_results_empty_ballot_abstention_does_not_count_as_voted(client):
    """A participant who submits cluster_ids: [] leaves zero Vote rows and
    must not be counted as having voted for the everyone-voted check -- if
    they're the only holdout, results stay 409 until the facilitator
    manually closes voting.
    """
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    token_y = _signup_and_login(client, "y@example.com")
    _add_member(client, project_id, "y@example.com", "team_member")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    _cast_votes(client, project_id, cycle_id, token, [cluster_a])
    abstain_response = _cast_votes(client, project_id, cycle_id, token_y, [])
    assert abstain_response.status_code == 200

    response = _get_vote_results(client, project_id, cycle_id, token)
    assert response.status_code == 409

    close_response = _close_voting(client, project_id, cycle_id, token)
    assert close_response.status_code == 200

    response_after_close = _get_vote_results(client, project_id, cycle_id, token)
    assert response_after_close.status_code == 200


def test_vote_results_available_after_manual_close_even_with_zero_votes(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    _create_cluster(client, project_id, cycle_id, token, name="A")

    close_response = _close_voting(client, project_id, cycle_id, token)
    assert close_response.status_code == 200

    response = _get_vote_results(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["vote_count"] == 0


def test_vote_results_ranked_descending_with_zero_vote_cluster_included(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    token_y = _signup_and_login(client, "y@example.com")
    _add_member(client, project_id, "y@example.com", "team_member")
    token_z = _signup_and_login(client, "z@example.com")
    _add_member(client, project_id, "z@example.com", "team_member")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_three = _create_cluster(client, project_id, cycle_id, token, name="Three")
    cluster_one = _create_cluster(client, project_id, cycle_id, token, name="One")
    cluster_zero = _create_cluster(client, project_id, cycle_id, token, name="Zero")

    with client.session_local() as db:
        user_x = db.query(User).filter(User.email == "facilitator@example.com").first()
        user_y = db.query(User).filter(User.email == "y@example.com").first()
        user_z = db.query(User).filter(User.email == "z@example.com").first()
        db.add_all(
            [
                Vote(cluster_id=cluster_three, participant_id=user_x.id),
                Vote(cluster_id=cluster_three, participant_id=user_y.id),
                Vote(cluster_id=cluster_three, participant_id=user_z.id),
                Vote(cluster_id=cluster_one, participant_id=user_x.id),
            ]
        )
        db.commit()

    close_response = _close_voting(client, project_id, cycle_id, token)
    assert close_response.status_code == 200

    response = _get_vote_results(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert [entry["cluster_id"] for entry in body] == [cluster_three, cluster_one, cluster_zero]
    assert [entry["vote_count"] for entry in body] == [3, 1, 0]
    for entry in body:
        assert set(entry.keys()) == {"cluster_id", "name", "vote_count", "discussion_status"}
        # #15 extends this response with discussion_status; a cluster that
        # has never had its status set defaults to "pending".
        assert entry["discussion_status"] == "pending"


def test_vote_results_tie_break_by_ascending_cluster_id(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_low = _create_cluster(client, project_id, cycle_id, token, name="First")
    cluster_high = _create_cluster(client, project_id, cycle_id, token, name="Second")
    assert cluster_low < cluster_high

    with client.session_local() as db:
        user_x = db.query(User).filter(User.email == "facilitator@example.com").first()
        db.add_all(
            [
                Vote(cluster_id=cluster_high, participant_id=user_x.id),
                Vote(cluster_id=cluster_low, participant_id=user_x.id),
            ]
        )
        db.commit()

    close_response = _close_voting(client, project_id, cycle_id, token)
    assert close_response.status_code == 200

    response = _get_vote_results(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert [entry["cluster_id"] for entry in body] == [cluster_low, cluster_high]
    assert [entry["vote_count"] for entry in body] == [1, 1]


def test_vote_results_remains_available_after_cycle_status_becomes_closed(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    _cast_votes(client, project_id, cycle_id, token, [cluster_a])
    close_response = _close_voting(client, project_id, cycle_id, token)
    assert close_response.status_code == 200

    _set_cycle_status(client, cycle_id, "closed")

    response = _get_vote_results(client, project_id, cycle_id, token)

    assert response.status_code == 200
    assert response.json()[0]["vote_count"] == 1


def test_vote_results_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/votes/results")

    assert response.status_code == 401


def test_vote_results_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _get_vote_results(client, project_id, cycle_id, outsider_token)

    assert response.status_code == 403


def test_vote_results_accessible_to_team_member_not_just_facilitator(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    token_member = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")
    cycle_id = _revealed_cycle(client, project_id, token)
    _create_cluster(client, project_id, cycle_id, token, name="A")
    _close_voting(client, project_id, cycle_id, token)

    response = _get_vote_results(client, project_id, cycle_id, token_member)

    assert response.status_code == 200


def test_vote_results_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _get_vote_results(client, project_id, 999999, token)

    assert response.status_code == 404


def test_vote_results_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "team_member")

    response = _get_vote_results(client, other_project_id, cycle_id, other_token)

    assert response.status_code == 404
