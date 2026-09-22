import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Cluster, FeedbackCard, FeedbackCycle, Project, ProjectMembership, Role, User


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


def _create_card(client: TestClient, project_id: int, cycle_id: int, token: str, text: str = "A card") -> int:
    """Creates a card while the cycle is open, then leaves the cycle's
    status untouched -- callers that need a revealed cycle set the status
    afterward via _set_cycle_status.
    """
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


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/clusters
# ---------------------------------------------------------------------------


def test_create_cluster_on_revealed_cycle_with_name_returns_201_with_id_cycle_id_name(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": "Communication"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["cycle_id"] == cycle_id
    assert body["name"] == "Communication"


def test_create_cluster_with_name_omitted_defaults_to_null(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    assert response.json()["name"] is None


def test_team_member_can_also_create_cluster(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": "From member"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 201


def test_create_cluster_on_open_cycle_is_409_and_creates_no_cluster(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": "Too early"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Cluster).count() == 0


def test_create_cluster_on_closed_cycle_is_409_and_creates_no_cluster(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    _set_cycle_status(client, cycle_id, "closed")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": "Too late"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Cluster).count() == 0


def test_create_cluster_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": "No auth"},
    )

    assert response.status_code == 401


def test_create_cluster_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        json={"name": "Not a member"},
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403
    with client.session_local() as db:
        assert db.query(Cluster).count() == 0


def test_create_cluster_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.post(
        f"/projects/{project_id}/cycles/999999/clusters",
        json={"name": "No such cycle"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_create_cluster_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = client.post(
        f"/projects/{other_project_id}/cycles/{cycle_id}/clusters",
        json={"name": "Wrong project"},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/cycles/{cycle_id}/clusters
# ---------------------------------------------------------------------------


def test_list_clusters_returns_every_cluster_with_id_cycle_id_name(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    _create_cluster(client, project_id, cycle_id, token, name="Alpha")
    _create_cluster(client, project_id, cycle_id, token, name="Beta")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    names = {c["name"] for c in body}
    assert names == {"Alpha", "Beta"}
    for cluster in body:
        assert cluster["cycle_id"] == cycle_id
        assert isinstance(cluster["id"], int)


def test_list_clusters_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_list_clusters_on_revealed_cycle_is_200(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_list_clusters_on_closed_cycle_still_returns_200(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    _create_cluster(client, project_id, cycle_id, token, name="Stays visible")
    _set_cycle_status(client, cycle_id, "closed")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "Stays visible"


def test_list_clusters_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.get(f"/projects/{project_id}/cycles/{cycle_id}/clusters")

    assert response.status_code == 401


def test_list_clusters_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


def test_list_clusters_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")

    response = client.get(
        f"/projects/{project_id}/cycles/999999/clusters",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_list_clusters_with_cycle_id_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")

    response = client.get(
        f"/projects/{other_project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}
# ---------------------------------------------------------------------------


def test_rename_cluster_succeeds_and_response_reflects_new_name(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Old name")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
        json={"name": "New name"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "New name"

    with client.session_local() as db:
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).first()
        assert cluster.name == "New name"


def test_team_member_can_also_rename_cluster(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Old name")

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
        json={"name": "Renamed by member"},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 200


def test_rename_cluster_with_blank_name_is_422_and_name_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Keep me")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
        json={"name": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    with client.session_local() as db:
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).first()
        assert cluster.name == "Keep me"


def test_rename_nonexistent_cluster_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/999999",
        json={"name": "No such cluster"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_rename_cluster_belonging_to_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Belongs elsewhere")

    response = client.patch(
        f"/projects/{project_id}/cycles/{other_cycle_id}/clusters/{cluster_id}",
        json={"name": "Wrong cycle"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    with client.session_local() as db:
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).first()
        assert cluster.name == "Belongs elsewhere"


def test_rename_cluster_belonging_to_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Mine")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _revealed_cycle(client, other_project_id, other_token)

    response = client.patch(
        f"/projects/{other_project_id}/cycles/{other_cycle_id}/clusters/{cluster_id}",
        json={"name": "Wrong project"},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


def test_rename_cluster_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Still open soon")
    _set_cycle_status(client, cycle_id, "open")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
        json={"name": "Too early"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_rename_cluster_on_closed_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Will be closed")
    _set_cycle_status(client, cycle_id, "closed")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
        json={"name": "Too late"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_rename_cluster_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Name")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
        json={"name": "No auth"},
    )

    assert response.status_code == 401


def test_rename_cluster_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Name")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
        json={"name": "Not a member"},
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


def test_rename_cluster_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Name")

    response = client.patch(
        f"/projects/{project_id}/cycles/999999/clusters/{cluster_id}",
        json={"name": "No such cycle"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster
# ---------------------------------------------------------------------------


def test_reassign_card_to_cluster_returns_serialized_card_with_updated_cluster_id(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": cluster_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == card_id
    assert body["cluster_id"] == cluster_id
    assert body["cycle_id"] == cycle_id
    assert body["category"] == "start"


def test_team_member_can_also_reassign_card_cluster(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": cluster_id},
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 200


def test_reassign_card_to_null_leaves_it_ungrouped_and_visible_in_all_cards(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": cluster_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["cluster_id"] is None

    all_cards = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    matching = next(card for card in all_cards if card["id"] == card_id)
    assert matching["cluster_id"] is None


def test_reassign_card_to_nonexistent_cluster_is_404_and_cluster_id_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": 999999},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.cluster_id is None


def test_reassign_card_to_cluster_from_a_different_cycle_is_404_and_cluster_id_unchanged(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    other_cycle_id = _revealed_cycle(client, project_id, token)
    foreign_cluster_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": foreign_cluster_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.cluster_id is None


def test_reassign_nonexistent_card_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/999999/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_reassign_card_id_belonging_to_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    other_cycle_id = _revealed_cycle(client, project_id, token)

    response = client.patch(
        f"/projects/{project_id}/cycles/{other_cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_reassign_card_id_belonging_to_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _revealed_cycle(client, other_project_id, other_token)

    response = client.patch(
        f"/projects/{other_project_id}/cycles/{other_cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


def test_reassign_card_cluster_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_reassign_card_cluster_on_closed_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "closed")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_reassign_card_cluster_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
    )

    assert response.status_code == 401


def test_reassign_card_cluster_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


def test_reassign_card_cluster_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)

    response = client.patch(
        f"/projects/{project_id}/cycles/999999/cards/{card_id}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}
# ---------------------------------------------------------------------------


def test_merge_moves_cards_and_removes_source_from_listing_and_returns_target(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_a = _create_card(client, project_id, cycle_id, token, text="Card A")
    card_b = _create_card(client, project_id, cycle_id, token, text="Card B")
    _set_cycle_status(client, cycle_id, "revealed")

    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_a}/cluster",
        json={"cluster_id": source_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_b}/cluster",
        json={"cluster_id": source_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == target_id
    assert body["name"] == "Target"

    with client.session_local() as db:
        card_a_row = db.query(FeedbackCard).filter(FeedbackCard.id == card_a).first()
        card_b_row = db.query(FeedbackCard).filter(FeedbackCard.id == card_b).first()
        assert card_a_row.cluster_id == target_id
        assert card_b_row.cluster_id == target_id

    listing = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    listed_ids = {c["id"] for c in listing}
    assert source_id not in listed_ids
    assert target_id in listed_ids


def test_team_member_can_also_merge_clusters(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    member_token = _signup_and_login(client, "member@example.com")
    _add_member(client, project_id, "member@example.com", "team_member")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert response.status_code == 200


def test_merge_cluster_into_itself_is_422_and_no_cards_moved(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_id = _create_card(client, project_id, cycle_id, token)
    _set_cycle_status(client, cycle_id, "revealed")
    cluster_id = _create_cluster(client, project_id, cycle_id, token, name="Solo")

    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
        json={"cluster_id": cluster_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/merge-into/{cluster_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    with client.session_local() as db:
        card = db.query(FeedbackCard).filter(FeedbackCard.id == card_id).first()
        assert card.cluster_id == cluster_id
        assert db.query(Cluster).filter(Cluster.id == cluster_id).first() is not None


def test_merge_with_nonexistent_source_is_404_and_no_cards_moved(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/999999/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_merge_with_nonexistent_target_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/999999",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    with client.session_local() as db:
        assert db.query(Cluster).filter(Cluster.id == source_id).first() is not None


def test_merge_with_cluster_from_a_different_cycle_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    other_cycle_id = _revealed_cycle(client, project_id, token)

    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    foreign_target_id = _create_cluster(client, project_id, other_cycle_id, token, name="Foreign target")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{foreign_target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


def test_merge_with_cluster_from_a_different_project_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    other_project_id, other_token = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _revealed_cycle(client, other_project_id, other_token)

    response = client.post(
        f"/projects/{other_project_id}/cycles/{other_cycle_id}/clusters/{source_id}/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404


def test_merge_on_open_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")
    _set_cycle_status(client, cycle_id, "open")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_merge_on_closed_cycle_is_409(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")
    _set_cycle_status(client, cycle_id, "closed")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409


def test_merge_without_token_is_401(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}",
    )

    assert response.status_code == 401


def test_merge_with_no_membership_is_403_not_500(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )

    assert response.status_code == 403


def test_merge_with_nonexistent_cycle_id_is_404(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _revealed_cycle(client, project_id, token)
    source_id = _create_cluster(client, project_id, cycle_id, token, name="Source")
    target_id = _create_cluster(client, project_id, cycle_id, token, name="Target")

    response = client.post(
        f"/projects/{project_id}/cycles/999999/clusters/{source_id}/merge-into/{target_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Split (no dedicated endpoint -- composed from create-cluster + reassign)
# ---------------------------------------------------------------------------


def test_split_is_achieved_by_creating_a_cluster_and_moving_a_subset_of_cards(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_a = _create_card(client, project_id, cycle_id, token, text="Card A")
    card_b = _create_card(client, project_id, cycle_id, token, text="Card B")
    card_c = _create_card(client, project_id, cycle_id, token, text="Card C")
    _set_cycle_status(client, cycle_id, "revealed")

    original_id = _create_cluster(client, project_id, cycle_id, token, name="Original")
    for card_id in (card_a, card_b, card_c):
        client.patch(
            f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster",
            json={"cluster_id": original_id},
            headers={"Authorization": f"Bearer {token}"},
        )

    # "Split" = create a new cluster, then move a subset of the original
    # cluster's cards into it -- no dedicated split endpoint exists.
    split_off_id = _create_cluster(client, project_id, cycle_id, token, name="Split off")
    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_c}/cluster",
        json={"cluster_id": split_off_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    with client.session_local() as db:
        a = db.query(FeedbackCard).filter(FeedbackCard.id == card_a).first()
        b = db.query(FeedbackCard).filter(FeedbackCard.id == card_b).first()
        c = db.query(FeedbackCard).filter(FeedbackCard.id == card_c).first()
        assert a.cluster_id == original_id
        assert b.cluster_id == original_id
        assert c.cluster_id == split_off_id

    listing = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    listed_ids = {c["id"] for c in listing}
    assert original_id in listed_ids
    assert split_off_id in listed_ids


# ---------------------------------------------------------------------------
# Persistence across a simulated reload
# ---------------------------------------------------------------------------


def test_persistence_across_reload_for_clusters_and_all_cards(client):
    project_id, token = _make_project_with_membership(client, "facilitator@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, token)
    card_1 = _create_card(client, project_id, cycle_id, token, text="Card 1")
    card_2 = _create_card(client, project_id, cycle_id, token, text="Card 2")
    card_3 = _create_card(client, project_id, cycle_id, token, text="Card 3")
    _set_cycle_status(client, cycle_id, "revealed")

    cluster_a = _create_cluster(client, project_id, cycle_id, token, name="A")
    cluster_b = _create_cluster(client, project_id, cycle_id, token, name="B")

    # Reassign cards, including one to null.
    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_1}/cluster",
        json={"cluster_id": cluster_a},
        headers={"Authorization": f"Bearer {token}"},
    )
    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_2}/cluster",
        json={"cluster_id": cluster_a},
        headers={"Authorization": f"Bearer {token}"},
    )
    client.patch(
        f"/projects/{project_id}/cycles/{cycle_id}/cards/{card_3}/cluster",
        json={"cluster_id": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Merge cluster_a into cluster_b.
    client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_a}/merge-into/{cluster_b}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Immediately after the mutations.
    clusters_after = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    cards_after = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    # A fresh GET (simulating a reload) of both listings, using a brand
    # new DB session under the hood via client.session_local -- the
    # in-memory objects from above are not reused.
    clusters_reloaded = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/clusters",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    cards_reloaded = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/cards",
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    def _by_id(items):
        return {item["id"]: item for item in items}

    assert _by_id(clusters_after) == _by_id(clusters_reloaded)
    assert _by_id(cards_after) == _by_id(cards_reloaded)

    reloaded_clusters_by_id = _by_id(clusters_reloaded)
    assert cluster_a not in reloaded_clusters_by_id
    assert cluster_b in reloaded_clusters_by_id

    reloaded_cards_by_id = _by_id(cards_reloaded)
    assert reloaded_cards_by_id[card_1]["cluster_id"] == cluster_b
    assert reloaded_cards_by_id[card_2]["cluster_id"] == cluster_b
    assert reloaded_cards_by_id[card_3]["cluster_id"] is None
