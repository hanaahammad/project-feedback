import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import FeedbackCycle, MeetingUpload, Project, ProjectMembership, Role, User


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

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


def _create_cycle(client: TestClient, project_id: int, status_value: str = "closed") -> int:
    with client.session_local() as db:
        cycle = FeedbackCycle(project_id=project_id, status=status_value)
        db.add(cycle)
        db.commit()
        return cycle.id


def _upload(client: TestClient, project_id: int, cycle_id: int, token: str | None, data: dict, files=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/uploads",
        data=data,
        files=files,
        headers=headers,
    )


def _list_uploads(client: TestClient, project_id: int, cycle_id: int, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(f"/projects/{project_id}/cycles/{cycle_id}/uploads", headers=headers)


def test_audio_upload_stores_file_and_creates_pending_row(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client,
        project_id,
        cycle_id,
        token,
        data={"kind": "audio"},
        files={"file": ("meeting.mp3", b"fake-audio-bytes", "audio/mpeg")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "audio"
    assert body["status"] == "pending"
    assert body["transcript_text"] is None

    with client.session_local() as db:
        row = db.query(MeetingUpload).filter(MeetingUpload.id == body["id"]).first()
        assert row.file_path is not None
        assert row.file_path.endswith("meeting.mp3")


def test_transcript_file_upload_stores_file(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client,
        project_id,
        cycle_id,
        token,
        data={"kind": "transcript_file"},
        files={"file": ("transcript.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 201
    assert response.json()["transcript_text"] is None


def test_transcript_text_upload_stores_text_and_no_file(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client,
        project_id,
        cycle_id,
        token,
        data={"kind": "transcript_text", "transcript_text": "We discussed the roadmap."},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["transcript_text"] == "We discussed the roadmap."

    with client.session_local() as db:
        row = db.query(MeetingUpload).filter(MeetingUpload.id == body["id"]).first()
        assert row.file_path is None


def test_audio_upload_with_no_file_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(client, project_id, cycle_id, token, data={"kind": "audio"})

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(MeetingUpload).count() == 0


def test_audio_upload_with_empty_file_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client,
        project_id,
        cycle_id,
        token,
        data={"kind": "audio"},
        files={"file": ("meeting.mp3", b"", "audio/mpeg")},
    )

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(MeetingUpload).count() == 0


def test_transcript_text_upload_with_blank_text_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client, project_id, cycle_id, token, data={"kind": "transcript_text", "transcript_text": "   "}
    )

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(MeetingUpload).count() == 0


def test_transcript_text_upload_with_missing_text_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(client, project_id, cycle_id, token, data={"kind": "transcript_text"})

    assert response.status_code == 422


def test_upload_with_both_file_and_transcript_text_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client,
        project_id,
        cycle_id,
        token,
        data={"kind": "audio", "transcript_text": "hello"},
        files={"file": ("meeting.mp3", b"fake-audio-bytes", "audio/mpeg")},
    )

    assert response.status_code == 422
    with client.session_local() as db:
        assert db.query(MeetingUpload).count() == 0


def test_upload_with_invalid_kind_is_422(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client,
        project_id,
        cycle_id,
        token,
        data={"kind": "screenshot", "transcript_text": "hello"},
    )

    assert response.status_code == 422


def test_upload_on_open_cycle_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="open")

    response = _upload(
        client, project_id, cycle_id, token, data={"kind": "transcript_text", "transcript_text": "hi"}
    )

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(MeetingUpload).count() == 0


def test_upload_on_revealed_cycle_is_409(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id, status_value="revealed")

    response = _upload(
        client, project_id, cycle_id, token, data={"kind": "transcript_text", "transcript_text": "hi"}
    )

    assert response.status_code == 409


def test_upload_from_team_member_is_403(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "member@example.com", "team_member")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client, project_id, cycle_id, token, data={"kind": "transcript_text", "transcript_text": "hi"}
    )

    assert response.status_code == 403


def test_upload_without_auth_is_401(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _upload(
        client, project_id, cycle_id, None, data={"kind": "transcript_text", "transcript_text": "hi"}
    )

    assert response.status_code == 401


def test_upload_without_membership_is_403(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _upload(
        client, project_id, cycle_id, outsider_token, data={"kind": "transcript_text", "transcript_text": "hi"}
    )

    assert response.status_code == 403


def test_upload_on_missing_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _upload(
        client, project_id, 999, token, data={"kind": "transcript_text", "transcript_text": "hi"}
    )

    assert response.status_code == 404


def test_upload_on_cycle_from_other_project_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    other_project_id, _ = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id)

    response = _upload(
        client, project_id, other_cycle_id, token, data={"kind": "transcript_text", "transcript_text": "hi"}
    )

    assert response.status_code == 404


def test_cycle_can_have_multiple_uploads_and_list_returns_both(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    _upload(client, project_id, cycle_id, token, data={"kind": "transcript_text", "transcript_text": "hi"})
    _upload(
        client,
        project_id,
        cycle_id,
        token,
        data={"kind": "audio"},
        files={"file": ("meeting.mp3", b"fake-audio-bytes", "audio/mpeg")},
    )

    response = _list_uploads(client, project_id, cycle_id, token)

    assert response.status_code == 200
    kinds = {row["kind"] for row in response.json()}
    assert kinds == {"transcript_text", "audio"}


def test_list_ordered_by_created_at_ascending(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    first = _upload(
        client, project_id, cycle_id, token, data={"kind": "transcript_text", "transcript_text": "first"}
    ).json()
    second = _upload(
        client, project_id, cycle_id, token, data={"kind": "transcript_text", "transcript_text": "second"}
    ).json()

    response = _list_uploads(client, project_id, cycle_id, token)

    ids = [row["id"] for row in response.json()]
    assert ids == [first["id"], second["id"]]


def test_list_on_cycle_with_no_uploads_returns_empty_list(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    open_cycle_id = _create_cycle(client, project_id, status_value="open")

    response = _list_uploads(client, project_id, open_cycle_id, token)

    assert response.status_code == 200
    assert response.json() == []


def test_list_without_auth_is_401(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _list_uploads(client, project_id, cycle_id, None)

    assert response.status_code == 401


def test_list_without_membership_is_403(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _list_uploads(client, project_id, cycle_id, outsider_token)

    assert response.status_code == 403


def test_list_on_missing_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _list_uploads(client, project_id, 999, token)

    assert response.status_code == 404


def test_list_on_cycle_from_other_project_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    other_project_id, _ = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id)

    response = _list_uploads(client, project_id, other_cycle_id, token)

    assert response.status_code == 404
