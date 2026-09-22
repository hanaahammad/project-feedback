import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import User, Project, Cycle, Cluster, Role, DiscussionStatusEnum
from app.db import get_db, Base, engine
from app.auth import create_access_token

client = TestClient(app)

@pytest.fixture(scope="module")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = Session(bind=engine)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def facilitator_user(db_session):
    user = User(username="facilitator", password_hash="pbkdf2:sha256:150000$dummy$dummy")
    db_session.add(user)
    db_session.commit()
    # Assign facilitator role
    project = Project(name="Test Project")
    db_session.add(project)
    db_session.commit()
    membership = ProjectMembership(user_id=user.id, project_id=project.id, role=Role.facilitator)
    db_session.add(membership)
    db_session.commit()
    return user, project

@pytest.fixture
def auth_token(facilitator_user):
    user, _ = facilitator_user
    return create_access_token({"sub": user.username})

@pytest.fixture
def cycle_and_cluster(db_session, facilitator_user):
    _, project = facilitator_user
    cycle = Cycle(project_id=project.id, status="revealed")
    db_session.add(cycle)
    db_session.commit()
    cluster = Cluster(cycle_id=cycle.id)
    db_session.add(cluster)
    db_session.commit()
    return cycle, cluster

def test_set_discussion_status_success(auth_token, cycle_and_cluster):
    cycle, cluster = cycle_and_cluster
    headers = {"Authorization": f"Bearer {auth_token}"}
    resp = client.patch(
        f"/api/projects/{cycle.project_id}/cycles/{cycle.id}/clusters/{cluster.id}/discussion-status",
        json={"status": "discussed"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cluster_id"] == cluster.id
    assert data["cycle_id"] == cycle.id
    assert data["status"] == "discussed"

def test_invalid_status(auth_token, cycle_and_cluster):
    cycle, cluster = cycle_and_cluster
    headers = {"Authorization": f"Bearer {auth_token}"}
    resp = client.patch(
        f"/api/projects/{cycle.project_id}/cycles/{cycle.id}/clusters/{cluster.id}/discussion-status",
        json={"status": "pending"},
        headers=headers,
    )
    assert resp.status_code == 422

def test_cycle_not_revealed(auth_token, db_session, facilitator_user):
    _, project = facilitator_user
    cycle = Cycle(project_id=project.id, status="open")
    db_session.add(cycle)
    db_session.commit()
    cluster = Cluster(cycle_id=cycle.id)
    db_session.add(cluster)
    db_session.commit()
    headers = {"Authorization": f"Bearer {auth_token}"}
    resp = client.patch(
        f"/api/projects/{project.id}/cycles/{cycle.id}/clusters/{cluster.id}/discussion-status",
        json={"status": "skipped"},
        headers=headers,
    )
    assert resp.status_code == 409
