import app.uploads as uploads_module
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import FeedbackCycle, MeetingUpload, Project, ProjectMembership, Role, User


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


def _create_cycle(client: TestClient, project_id: int, status_value: str = "closed") -> int:
    with client.session_local() as db:
        cycle = FeedbackCycle(project_id=project_id, status=status_value)
        db.add(cycle)
        db.commit()
        return cycle.id


def _create_upload(
    client: TestClient,
    cycle_id: int,
    kind: str = "audio",
    status_value: str = "pending",
    file_path: str | None = "uploads/1/rec.mp3",
    transcript_text: str | None = None,
) -> int:
    with client.session_local() as db:
        upload = MeetingUpload(
            cycle_id=cycle_id,
            kind=kind,
            status=status_value,
            file_path=file_path,
            transcript_text=transcript_text,
        )
        db.add(upload)
        db.commit()
        return upload.id


def _transcribe(client: TestClient, project_id: int, cycle_id: int, upload_id: int, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/transcribe",
        headers=headers,
    )


def test_successful_transcription_completes_and_sets_transcript_text(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="audio")

    monkeypatch.setattr(uploads_module, "transcribe_audio", lambda file_path: "We discussed the roadmap.")

    response = _transcribe(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["transcript_text"] == "We discussed the roadmap."

    listing = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/uploads",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert listing[0]["status"] == "complete"
    assert listing[0]["transcript_text"] == "We discussed the roadmap."


def test_failed_transcription_sets_status_failed_and_returns_200(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="video")

    def fake_transcribe(file_path):
        raise RuntimeError("provider timed out")

    monkeypatch.setattr(uploads_module, "transcribe_audio", fake_transcribe)

    response = _transcribe(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["transcript_text"] is None

    listing = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/uploads",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert listing[0]["status"] == "failed"
    assert listing[0]["transcript_text"] is None


def test_transcribe_on_transcript_file_kind_is_422(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="transcript_file")

    monkeypatch.setattr(uploads_module, "transcribe_audio", lambda file_path: "should not be called")

    response = _transcribe(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 422
    with client.session_local() as db:
        row = db.query(MeetingUpload).filter(MeetingUpload.id == upload_id).first()
        assert row.status == "pending"
        assert row.transcript_text is None


def test_transcribe_on_transcript_text_kind_is_422(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(
        client, cycle_id, kind="transcript_text", file_path=None, transcript_text="pasted text"
    )

    monkeypatch.setattr(uploads_module, "transcribe_audio", lambda file_path: "should not be called")

    response = _transcribe(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 422
    with client.session_local() as db:
        row = db.query(MeetingUpload).filter(MeetingUpload.id == upload_id).first()
        assert row.status == "pending"
        assert row.transcript_text == "pasted text"


@pytest.mark.parametrize("existing_status", ["processing", "complete", "failed"])
def test_transcribe_on_non_pending_upload_is_409(client: TestClient, monkeypatch, existing_status) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="audio", status_value=existing_status)

    monkeypatch.setattr(uploads_module, "transcribe_audio", lambda file_path: "should not be called")

    response = _transcribe(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        row = db.query(MeetingUpload).filter(MeetingUpload.id == upload_id).first()
        assert row.status == existing_status


def test_transcribing_an_already_completed_upload_a_second_time_is_409(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="audio")

    monkeypatch.setattr(uploads_module, "transcribe_audio", lambda file_path: "first pass")
    first = _transcribe(client, project_id, cycle_id, upload_id, token)
    assert first.status_code == 200
    assert first.json()["status"] == "complete"

    second = _transcribe(client, project_id, cycle_id, upload_id, token)
    assert second.status_code == 409


def test_transcribe_on_missing_upload_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _transcribe(client, project_id, cycle_id, 999, token)

    assert response.status_code == 404


def test_transcribe_on_upload_from_different_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    other_cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, other_cycle_id, kind="audio")

    response = _transcribe(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 404


def test_transcribe_on_missing_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _transcribe(client, project_id, 999, 1, token)

    assert response.status_code == 404


def test_transcribe_on_cycle_from_other_project_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    other_project_id, _ = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id)
    upload_id = _create_upload(client, other_cycle_id, kind="audio")

    response = _transcribe(client, project_id, other_cycle_id, upload_id, token)

    assert response.status_code == 404


def test_transcribe_from_team_member_is_403(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "member@example.com", "team_member")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="audio")

    response = _transcribe(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 403


def test_transcribe_without_auth_is_401(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="audio")

    response = _transcribe(client, project_id, cycle_id, upload_id, None)

    assert response.status_code == 401


def test_transcribe_without_membership_is_403(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, kind="audio")
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _transcribe(client, project_id, cycle_id, upload_id, outsider_token)

    assert response.status_code == 403


def test_real_transcribe_audio_raises_not_implemented_error() -> None:
    from app.transcription import transcribe_audio

    with pytest.raises(NotImplementedError):
        transcribe_audio("uploads/1/rec.mp3")
