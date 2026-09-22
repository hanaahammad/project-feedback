import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.clusters as clusters_module
from app.ai_clustering import SuggestedGroup
from app.db import Base, get_db
from app.main import app
from app.models import Cluster, FeedbackCard, FeedbackCycle, Project, ProjectMembership, Role, User

# ---------------------------------------------------------------------------
# Fixtures / helpers -- mirrors tests/test_clusters.py's patterns exactly so
# this file behaves like a natural extension of that test module.
# ---------------------------------------------------------------------------


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


def _create_card(client: TestClient, project_id: int, cycle_id: int, token: str, text: str = "A card") -> int:
    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        json={"category": "start", "text": text},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


def _create_cluster(client: TestClient, project_id: int, cycle_id: int, token: str, name: str | None = None) -> int:
    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    return response.json()["id"]


def _suggest_clusters(client: TestClient, project_id: int, cycle_id: int, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/suggest-clusters",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/suggest-clusters -- success
# ---------------------------------------------------------------------------


def test_suggest_clusters_applies_fake_grouping_creates_clusters_and_reassigns_cards(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_a = _create_card(client, project_id, cycle_id, token, text="Card A")
    card_b = _create_card(client, project_id, cycle_id, token, text="Card B")
    card_c = _create_card(client, project_id, cycle_id, token, text="Card C")
    _set_cycle_status(client, cycle_id, "revealed")

    def fake_generate(cards):
        ids = {c.id for c in cards}
        assert ids == {card_a, card_b, card_c}
        return [
            SuggestedGroup(name="Group One", card_ids=[card_a, card_b]),
            SuggestedGroup(name=None, card_ids=[card_c]),
        ]

    monkeypatch.setattr(clusters_module, "generate_cluster_suggestions", fake_generate)

    response = _suggest_clusters(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert len(body["clusters"]) == 2
    for cluster in body["clusters"]:
        assert isinstance(cluster["id"], int)
        assert cluster["cycle_id"] == cycle_id
    names = {c["name"] for c in body["clusters"]}
    assert names == {"Group One", None}

    # Clusters persist and are visible via #11's GET endpoint.
    listing = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert len(listing) == 2
    listed_ids = {c["id"] for c in listing}
    assert listed_ids == {c["id"] for c in body["clusters"]}

    # Cards reflect their new cluster_id via #10's GET endpoint.
    cards = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    by_id = {c["id"]: c for c in cards}

    group_one_id = next(c["id"] for c in body["clusters"] if c["name"] == "Group One")
    group_two_id = next(c["id"] for c in body["clusters"] if c["name"] is None)
    assert by_id[card_a]["cluster_id"] == group_one_id
    assert by_id[card_b]["cluster_id"] == group_one_id
    assert by_id[card_c]["cluster_id"] == group_two_id


def test_suggest_clusters_only_groups_currently_ungrouped_cards(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    already_clustered_card = _create_card(client, project_id, cycle_id, token, text="Already grouped")
    ungrouped_card = _create_card(client, project_id, cycle_id, token, text="Still ungrouped")
    _set_cycle_status(client, cycle_id, "revealed")

    manual_cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Manual")
    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{already_clustered_card}/cluster",
        json={"cluster_id": manual_cluster_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    received_card_ids = []

    def fake_generate(cards):
        received_card_ids.extend(c.id for c in cards)
        return [SuggestedGroup(name="Suggested", card_ids=[c.id for c in cards])]

    monkeypatch.setattr(clusters_module, "generate_cluster_suggestions", fake_generate)

    response = _suggest_clusters(client, project_id, cycle_id, token)

    assert response.status_code == 200
    # Only the ungrouped card was passed to the AI function.
    assert received_card_ids == [ungrouped_card]

    with client.session_local() as db:
        already = db.query(FeedbackCard).filter(FeedbackCard.id == already_clustered_card).first()
        now_grouped = db.query(FeedbackCard).filter(FeedbackCard.id == ungrouped_card).first()
        assert already.cluster_id == manual_cluster_id  # untouched
        assert now_grouped.cluster_id is not None
        assert now_grouped.cluster_id != manual_cluster_id


def test_suggest_clusters_empty_grouping_is_applied_with_no_clusters(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    monkeypatch.setattr(clusters_module, "generate_cluster_suggestions", lambda cards: [])

    response = _suggest_clusters(client, project_id, cycle_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["clusters"] == []


def test_team_member_can_also_trigger_suggest_clusters(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    monkeypatch.setattr(clusters_module, "generate_cluster_suggestions", lambda cards: [])

    response = _suggest_clusters(client, project_id, cycle_id, member_token)

    assert response.status_code == 200


def test_suggested_cluster_is_an_ordinary_cluster_renamable_and_cards_reassignable(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    monkeypatch.setattr(
        clusters_module,
        "generate_cluster_suggestions",
        lambda cards: [SuggestedGroup(name="AI Suggested", card_ids=[c.id for c in cards])],
    )

    response = _suggest_clusters(client, project_id, cycle_id, token)
    suggested_cluster_id = response.json()["clusters"][0]["id"]

    # Renamed via #11's PATCH .../clusters/{cluster_id} -- no special-casing.
    rename_response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{suggested_cluster_id}",
        json={"name": "Renamed manually"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rename_response.status_code == 200
    assert rename_response.json()["name"] == "Renamed manually"

    # Card reassigned out of it, including back to null, via #11's PATCH
    # .../cards/{card_id}/cluster -- no rejection.
    unassign_response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unassign_response.status_code == 200
    assert unassign_response.json()["cluster_id"] is None

    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.cluster_id is None
        cluster = db.query(Cluster).filter(Cluster.id == suggested_cluster_id).first()
        assert cluster.name == "Renamed manually"


# ---------------------------------------------------------------------------
# POST .../suggest-clusters -- failure path
# ---------------------------------------------------------------------------


def test_suggest_clusters_when_ai_function_raises_returns_200_unavailable_with_no_side_effects(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    def fake_generate(cards):
        raise RuntimeError("simulated AI provider timeout")

    monkeypatch.setattr(clusters_module, "generate_cluster_suggestions", fake_generate)

    response = _suggest_clusters(client, project_id, cycle_id, token)

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "clusters": []}

    with client.session_local() as db:
        assert db.query(Cluster).count() == 0
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.cluster_id is None


def test_suggest_clusters_unconfigured_default_implementation_returns_unavailable(client):
    """No monkeypatch at all -- exercises the real app.ai_clustering.generate_cluster_suggestions,
    which raises NotImplementedError because no provider is wired up. Confirms this never
    makes a real network call (none is possible -- no SDK is installed) and never surfaces
    as an error to the caller.
    """
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _suggest_clusters(client, project_id, cycle_id, token)

    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "clusters": []}
    with client.session_local() as db:
        assert db.query(Cluster).count() == 0


# ---------------------------------------------------------------------------
# POST .../suggest-clusters -- gating / auth
# ---------------------------------------------------------------------------


def test_suggest_clusters_on_open_cycle_is_409_and_no_side_effects(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    monkeypatch.setattr(
        clusters_module,
        "generate_cluster_suggestions",
        lambda cards: [SuggestedGroup(name="Should not run", card_ids=[])],
    )

    response = _suggest_clusters(client, project_id, cycle_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Cluster).count() == 0


def test_suggest_clusters_on_closed_cycle_is_409_and_no_side_effects(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "closed")

    monkeypatch.setattr(
        clusters_module,
        "generate_cluster_suggestions",
        lambda cards: [SuggestedGroup(name="Should not run", card_ids=[])],
    )

    response = _suggest_clusters(client, project_id, cycle_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Cluster).count() == 0


def test_suggest_clusters_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = _suggest_clusters(client, project_id, 999999, token)

    assert response.status_code == 404


def test_suggest_clusters_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = _suggest_clusters(client, other_project_id, cycle_id, other_token)

    assert response.status_code == 404


def test_suggest_clusters_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = _suggest_clusters(client, project_id, cycle_id, None)

    assert response.status_code == 401


def test_suggest_clusters_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _suggest_clusters(client, project_id, cycle_id, outsider_token)

    assert response.status_code == 403
    with client.session_local() as db:
        assert db.query(Cluster).count() == 0


# ---------------------------------------------------------------------------
# Reveal (#10) never touches the AI function / needs no AI config
# ---------------------------------------------------------------------------


def test_reveal_never_calls_ai_function_and_succeeds_with_no_ai_config(client, monkeypatch):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    def exploding_generate(cards):
        raise AssertionError("reveal must never call generate_cluster_suggestions")

    monkeypatch.setattr(clusters_module, "generate_cluster_suggestions", exploding_generate)

    # No AI-related environment variable or provider configured at all --
    # this test process has none, and reveal must not need any.
    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/reveal",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "revealed"
