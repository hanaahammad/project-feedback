import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import (
    ActionItem,
    Cluster,
    Decision,
    DiscussionNote,
    FeedbackCard,
    FeedbackCycle,
    Project,
    ProjectMembership,
    Role,
    User,
    Vote,
)


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


def _create_cycle(client: TestClient, project_id: int, status_value: str = "closed", ai_summary: str | None = None, summary_confirmed: bool = False) -> int:
    with client.session_local() as db:
        cycle = FeedbackCycle(
            project_id=project_id, status=status_value, ai_summary=ai_summary, summary_confirmed=summary_confirmed
        )
        db.add(cycle)
        db.commit()
        return cycle.id


def _get_summary(client: TestClient, project_id: int, cycle_id: int, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/projects/{project_id}/cycles/{cycle_id}/summary", headers=headers)


def test_summary_on_open_cycle_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="open")

    response = _get_summary(client, project_id, cycle_id, token)

    assert response.status_code == 409


def test_summary_on_revealed_cycle_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="revealed")

    response = _get_summary(client, project_id, cycle_id, token)

    assert response.status_code == 409


def test_empty_closed_cycle_returns_all_empty(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _get_summary(client, project_id, cycle_id, token)

    assert response.status_code == 200
    assert response.json() == {
        "top_discussion_topics": [],
        "notes": [],
        "confirmed_decisions": [],
        "confirmed_action_items": [],
        "attendance": [],
        "original_cards": [],
        "summary": None,
    }


def test_top_discussion_topics_ranked_by_votes_ties_broken_by_id(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    member_a = _add_member(client, project_id, "a@example.com", "team_member")
    member_b = _add_member(client, project_id, "b@example.com", "team_member")
    member_c = _add_member(client, project_id, "c@example.com", "team_member")
    a_id, b_id, c_id = (_user_id(client, e) for e in ["a@example.com", "b@example.com", "c@example.com"])

    cycle_id = _create_cycle(client, project_id)
    with client.session_local() as db:
        cluster_3votes = Cluster(cycle_id=cycle_id, name="Popular")
        cluster_1vote = Cluster(cycle_id=cycle_id, name="Mild")
        cluster_0votes = Cluster(cycle_id=cycle_id, name="Ignored")
        db.add_all([cluster_3votes, cluster_1vote, cluster_0votes])
        db.flush()
        db.add_all(
            [
                Vote(cluster_id=cluster_3votes.id, participant_id=a_id),
                Vote(cluster_id=cluster_3votes.id, participant_id=b_id),
                Vote(cluster_id=cluster_3votes.id, participant_id=c_id),
                Vote(cluster_id=cluster_1vote.id, participant_id=a_id),
            ]
        )
        db.commit()
        popular_id, mild_id, ignored_id = cluster_3votes.id, cluster_1vote.id, cluster_0votes.id

    response = _get_summary(client, project_id, cycle_id, token)

    topics = response.json()["top_discussion_topics"]
    assert [t["cluster_id"] for t in topics] == [popular_id, mild_id, ignored_id]
    assert [t["vote_count"] for t in topics] == [3, 1, 0]


def test_top_discussion_topics_available_without_close_voting_or_full_participation(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _add_member(client, project_id, "a@example.com", "team_member")
    _add_member(client, project_id, "b@example.com", "team_member")
    a_id = _user_id(client, "a@example.com")

    cycle_id = _create_cycle(client, project_id)
    with client.session_local() as db:
        cluster = Cluster(cycle_id=cycle_id, name="Topic")
        db.add(cluster)
        db.flush()
        db.add(Vote(cluster_id=cluster.id, participant_id=a_id))
        # voting_closed left False, and "b" never voted
        db.commit()
        cluster_id = cluster.id

    response = _get_summary(client, project_id, cycle_id, token)

    assert response.status_code == 200
    topics = response.json()["top_discussion_topics"]
    assert topics == [{"cluster_id": cluster_id, "name": "Topic", "vote_count": 1, "discussion_status": "pending"}]


def test_notes_ordered_ascending(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    fac_id = _user_id(client, "fac@example.com")
    cycle_id = _create_cycle(client, project_id)
    with client.session_local() as db:
        cluster = Cluster(cycle_id=cycle_id)
        db.add(cluster)
        db.flush()
        first = DiscussionNote(cluster_id=cluster.id, author_id=fac_id, text="First")
        db.add(first)
        db.flush()
        second = DiscussionNote(cluster_id=cluster.id, author_id=fac_id, text="Second")
        db.add(second)
        db.commit()
        first_id, second_id = first.id, second.id

    response = _get_summary(client, project_id, cycle_id, token)

    notes = response.json()["notes"]
    assert [n["id"] for n in notes] == [first_id, second_id]


def test_only_confirmed_decisions_included(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    with client.session_local() as db:
        db.add(Decision(cycle_id=cycle_id, description="Confirmed one", confirmed=True))
        db.add(Decision(cycle_id=cycle_id, description="Draft", confirmed=False))
        db.commit()

    response = _get_summary(client, project_id, cycle_id, token)

    decisions = response.json()["confirmed_decisions"]
    assert [d["description"] for d in decisions] == ["Confirmed one"]


def test_only_confirmed_action_items_included(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    with client.session_local() as db:
        db.add(ActionItem(cycle_id=cycle_id, description="Confirmed one", status="open", confirmed=True))
        db.add(ActionItem(cycle_id=cycle_id, description="Draft", status="open", confirmed=False))
        db.commit()

    response = _get_summary(client, project_id, cycle_id, token)

    items = response.json()["confirmed_action_items"]
    assert [i["description"] for i in items] == ["Confirmed one"]


def test_summary_null_when_not_confirmed_even_if_ai_summary_set(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="Drafted text", summary_confirmed=False)

    response = _get_summary(client, project_id, cycle_id, token)

    assert response.json()["summary"] is None


def test_summary_present_when_confirmed(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="Final text", summary_confirmed=True)

    response = _get_summary(client, project_id, cycle_id, token)

    assert response.json()["summary"] == "Final text"


def test_response_has_no_summary_confirmed_flag_or_draft_leak(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="Drafted text", summary_confirmed=False)
    with client.session_local() as db:
        db.add(Decision(cycle_id=cycle_id, description="Unconfirmed decision", confirmed=False))
        db.add(ActionItem(cycle_id=cycle_id, description="Unconfirmed item", status="open", confirmed=False))
        db.commit()

    response = _get_summary(client, project_id, cycle_id, token)

    body = response.json()
    assert "summary_confirmed" not in body
    assert body["confirmed_decisions"] == []
    assert body["confirmed_action_items"] == []
    assert "Drafted text" not in str(body)


def test_attendance_includes_card_authors_and_voters_not_neither(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    member_a_token = _add_member(client, project_id, "a@example.com", "team_member")
    _add_member(client, project_id, "b@example.com", "team_member")
    _add_member(client, project_id, "c@example.com", "team_member")
    a_id, b_id = _user_id(client, "a@example.com"), _user_id(client, "b@example.com")

    open_cycle_id = _create_cycle(client, project_id, status_value="open")
    with client.session_local() as db:
        db.add(FeedbackCard(cycle_id=open_cycle_id, author_id=a_id, category="start", text="Card from A"))
        cluster = Cluster(cycle_id=open_cycle_id)
        db.add(cluster)
        db.flush()
        db.add(Vote(cluster_id=cluster.id, participant_id=b_id))
        db.commit()
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == open_cycle_id).first()
        cycle.status = "closed"
        db.commit()

    response = _get_summary(client, project_id, open_cycle_id, token)

    attendance = response.json()["attendance"]
    assert attendance == sorted([a_id, b_id])


def test_anonymous_card_author_hidden_but_attendance_still_includes_them(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _add_member(client, project_id, "a@example.com", "team_member")
    a_id = _user_id(client, "a@example.com")

    cycle_id = _create_cycle(client, project_id, status_value="open")
    with client.session_local() as db:
        db.add(
            FeedbackCard(
                cycle_id=cycle_id, author_id=a_id, category="start", text="Anon card", is_anonymous=True
            )
        )
        cluster = Cluster(cycle_id=cycle_id)
        db.add(cluster)
        db.flush()
        db.add(Vote(cluster_id=cluster.id, participant_id=a_id))
        db.commit()
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        cycle.status = "closed"
        db.commit()

    response = _get_summary(client, project_id, cycle_id, token)

    body = response.json()
    assert a_id in body["attendance"]
    assert len(body["original_cards"]) == 1
    assert "author_id" not in body["original_cards"][0]


def test_original_cards_include_every_author_ordered(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _add_member(client, project_id, "a@example.com", "team_member")
    _add_member(client, project_id, "b@example.com", "team_member")
    a_id, b_id = _user_id(client, "a@example.com"), _user_id(client, "b@example.com")

    cycle_id = _create_cycle(client, project_id, status_value="open")
    with client.session_local() as db:
        first = FeedbackCard(cycle_id=cycle_id, author_id=a_id, category="start", text="From A")
        db.add(first)
        db.flush()
        second = FeedbackCard(cycle_id=cycle_id, author_id=b_id, category="stop", text="From B")
        db.add(second)
        db.commit()
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        cycle.status = "closed"
        db.commit()
        first_id, second_id = first.id, second.id

    response = _get_summary(client, project_id, cycle_id, token)

    cards = response.json()["original_cards"]
    assert [c["id"] for c in cards] == [first_id, second_id]
    assert cards[0]["author_id"] == a_id
    assert cards[1]["author_id"] == b_id


def test_team_member_can_call_endpoint(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    member_token = _add_member(client, project_id, "member@example.com", "team_member")
    cycle_id = _create_cycle(client, project_id)

    response = _get_summary(client, project_id, cycle_id, member_token)

    assert response.status_code == 200


def test_endpoint_is_read_only_and_idempotent(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, ai_summary="s", summary_confirmed=True)
    with client.session_local() as db:
        db.add(Decision(cycle_id=cycle_id, description="D", confirmed=True))
        db.add(ActionItem(cycle_id=cycle_id, description="A", status="open", confirmed=True))
        db.commit()

    def counts():
        with client.session_local() as db:
            return (
                db.query(Decision).count(),
                db.query(ActionItem).count(),
                db.query(DiscussionNote).count(),
                db.query(FeedbackCard).count(),
                db.query(Vote).count(),
                db.query(Cluster).count(),
            )

    before = counts()
    first = _get_summary(client, project_id, cycle_id, token)
    second = _get_summary(client, project_id, cycle_id, token)
    after = counts()

    assert first.json() == second.json()
    assert before == after


def test_summary_without_auth_is_401(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _get_summary(client, project_id, cycle_id, None)

    assert response.status_code == 401


def test_summary_without_membership_is_403(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _get_summary(client, project_id, cycle_id, outsider_token)

    assert response.status_code == 403


def test_summary_missing_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _get_summary(client, project_id, 999, token)

    assert response.status_code == 404


def test_summary_cycle_from_other_project_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    other_project_id, _ = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id)

    response = _get_summary(client, project_id, other_cycle_id, token)

    assert response.status_code == 404
