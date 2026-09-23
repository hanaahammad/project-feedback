from datetime import date

import app.uploads as uploads_module
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai_extraction import ExtractedActionItem, ExtractionResult
from app.db import Base, get_db
from app.main import app
from app.models import ActionItem, Decision, FeedbackCycle, MeetingUpload, Project, ProjectMembership, Role, User


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
    _signup_and_login(client, email)
    with client.session_local() as db:
        user = db.query(User).filter(User.email == email).first()
        role = db.query(Role).filter(Role.name == role_name).first()
        db.add(ProjectMembership(user_id=user.id, project_id=project_id, role_id=role.id))
        db.commit()


def _create_cycle(client: TestClient, project_id: int, status_value: str = "closed") -> int:
    with client.session_local() as db:
        cycle = FeedbackCycle(project_id=project_id, status=status_value)
        db.add(cycle)
        db.commit()
        return cycle.id


def _create_upload(client: TestClient, cycle_id: int, status_value: str = "complete", transcript_text: str | None = "Transcript.") -> int:
    with client.session_local() as db:
        upload = MeetingUpload(
            cycle_id=cycle_id,
            kind="audio",
            status=status_value,
            transcript_text=transcript_text,
            file_path="uploads/1/rec.mp3",
        )
        db.add(upload)
        db.commit()
        return upload.id


def _extract(client: TestClient, project_id: int, cycle_id: int, upload_id: int, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        f"/projects/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/extract",
        headers=headers,
    )


def test_successful_extraction_creates_unconfirmed_rows_and_sets_summary(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    result = ExtractionResult(
        decisions=["Ship the new onboarding flow"],
        action_items=[ExtractedActionItem(description="Write docs", due_date=date(2026, 10, 1))],
        summary="Team agreed to ship onboarding.",
    )
    monkeypatch.setattr(uploads_module, "extract_decisions_and_actions", lambda transcript_text: result)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "extracted"
    assert body["summary"] == "Team agreed to ship onboarding."
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["description"] == "Ship the new onboarding flow"
    assert body["decisions"][0]["confirmed"] is False
    assert body["decisions"][0]["author_id"] is None
    assert len(body["action_items"]) == 1
    assert body["action_items"][0]["description"] == "Write docs"
    assert body["action_items"][0]["due_date"] == "2026-10-01"
    assert body["action_items"][0]["confirmed"] is False
    assert body["action_items"][0]["owner_id"] is None

    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.ai_summary == "Team agreed to ship onboarding."
        assert db.query(Decision).count() == 1
        assert db.query(ActionItem).count() == 1


def test_owner_email_resolves_to_owner_id_when_member_matches(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _add_member(client, project_id, "bob@example.com", "team_member")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    result = ExtractionResult(
        action_items=[ExtractedActionItem(description="Follow up", owner_email="bob@example.com")],
        summary="s",
    )
    monkeypatch.setattr(uploads_module, "extract_decisions_and_actions", lambda transcript_text: result)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    with client.session_local() as db:
        bob = db.query(User).filter(User.email == "bob@example.com").first()

    assert response.json()["action_items"][0]["owner_id"] == bob.id


def test_owner_email_with_no_matching_user_leaves_owner_id_null(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    result = ExtractionResult(
        action_items=[ExtractedActionItem(description="Follow up", owner_email="ghost@example.com")],
        summary="s",
    )
    monkeypatch.setattr(uploads_module, "extract_decisions_and_actions", lambda transcript_text: result)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.json()["action_items"][0]["owner_id"] is None


def test_owner_email_matching_user_without_membership_leaves_owner_id_null(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    _signup_and_login(client, "outsider@example.com")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    result = ExtractionResult(
        action_items=[ExtractedActionItem(description="Follow up", owner_email="outsider@example.com")],
        summary="s",
    )
    monkeypatch.setattr(uploads_module, "extract_decisions_and_actions", lambda transcript_text: result)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.json()["action_items"][0]["owner_id"] is None


def test_blank_decision_and_action_item_are_skipped(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    result = ExtractionResult(
        decisions=["Valid decision", "   "],
        action_items=[
            ExtractedActionItem(description="Valid action"),
            ExtractedActionItem(description=""),
        ],
        summary="s",
    )
    monkeypatch.setattr(uploads_module, "extract_decisions_and_actions", lambda transcript_text: result)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    body = response.json()
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["description"] == "Valid decision"
    assert len(body["action_items"]) == 1
    assert body["action_items"][0]["description"] == "Valid action"


def test_ai_summary_overwritten_not_accumulated(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    monkeypatch.setattr(
        uploads_module,
        "extract_decisions_and_actions",
        lambda transcript_text: ExtractionResult(summary="First summary"),
    )
    _extract(client, project_id, cycle_id, upload_id, token)

    monkeypatch.setattr(
        uploads_module,
        "extract_decisions_and_actions",
        lambda transcript_text: ExtractionResult(summary="Second summary"),
    )
    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.json()["summary"] == "Second summary"
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.ai_summary == "Second summary"


def test_failed_extraction_returns_200_unavailable_with_no_side_effects(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    def fake_extract(transcript_text):
        raise RuntimeError("provider timed out")

    monkeypatch.setattr(uploads_module, "extract_decisions_and_actions", fake_extract)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "unavailable", "summary": None, "decisions": [], "action_items": []}

    with client.session_local() as db:
        assert db.query(Decision).count() == 0
        assert db.query(ActionItem).count() == 0
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.ai_summary is None


def test_failed_extraction_leaves_prior_summary_unchanged(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    monkeypatch.setattr(
        uploads_module,
        "extract_decisions_and_actions",
        lambda transcript_text: ExtractionResult(summary="Kept summary"),
    )
    _extract(client, project_id, cycle_id, upload_id, token)

    def fake_extract(transcript_text):
        raise RuntimeError("boom")

    monkeypatch.setattr(uploads_module, "extract_decisions_and_actions", fake_extract)
    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.json()["status"] == "unavailable"
    with client.session_local() as db:
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.ai_summary == "Kept summary"


def test_extraction_is_additive_across_calls(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    monkeypatch.setattr(
        uploads_module,
        "extract_decisions_and_actions",
        lambda transcript_text: ExtractionResult(decisions=["First decision"], summary="s1"),
    )
    _extract(client, project_id, cycle_id, upload_id, token)

    monkeypatch.setattr(
        uploads_module,
        "extract_decisions_and_actions",
        lambda transcript_text: ExtractionResult(decisions=["Second decision"], summary="s2"),
    )
    _extract(client, project_id, cycle_id, upload_id, token)

    with client.session_local() as db:
        descriptions = {row.description for row in db.query(Decision).all()}
        assert descriptions == {"First decision", "Second decision"}
        cycle = db.query(FeedbackCycle).filter(FeedbackCycle.id == cycle_id).first()
        assert cycle.ai_summary == "s2"


@pytest.mark.parametrize("upload_status", ["pending", "processing", "failed"])
def test_extract_on_non_complete_upload_is_409(client: TestClient, monkeypatch, upload_status) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id, status_value=upload_status, transcript_text=None)

    monkeypatch.setattr(
        uploads_module,
        "extract_decisions_and_actions",
        lambda transcript_text: ExtractionResult(summary="should not be called"),
    )

    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 409
    with client.session_local() as db:
        assert db.query(Decision).count() == 0
        assert db.query(ActionItem).count() == 0


def test_extract_on_missing_upload_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)

    response = _extract(client, project_id, cycle_id, 999, token)

    assert response.status_code == 404


def test_extract_on_upload_from_different_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    other_cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, other_cycle_id)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 404


def test_extract_on_missing_cycle_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")

    response = _extract(client, project_id, 999, 1, token)

    assert response.status_code == 404


def test_extract_on_cycle_from_other_project_is_404(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    other_project_id, _ = _make_project_with_membership(client, "other@example.com", "facilitator")
    other_cycle_id = _create_cycle(client, other_project_id)
    upload_id = _create_upload(client, other_cycle_id)

    response = _extract(client, project_id, other_cycle_id, upload_id, token)

    assert response.status_code == 404


def test_extract_from_team_member_is_403(client: TestClient) -> None:
    project_id, token = _make_project_with_membership(client, "member@example.com", "team_member")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    response = _extract(client, project_id, cycle_id, upload_id, token)

    assert response.status_code == 403


def test_extract_without_auth_is_401(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    response = _extract(client, project_id, cycle_id, upload_id, None)

    assert response.status_code == 401


def test_extract_without_membership_is_403(client: TestClient) -> None:
    project_id, _ = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)
    outsider_token = _signup_and_login(client, "outsider@example.com")

    response = _extract(client, project_id, cycle_id, upload_id, outsider_token)

    assert response.status_code == 403


def test_uploads_list_unaffected_by_extraction(client: TestClient, monkeypatch) -> None:
    project_id, token = _make_project_with_membership(client, "fac@example.com", "facilitator")
    cycle_id = _create_cycle(client, project_id)
    upload_id = _create_upload(client, cycle_id)

    monkeypatch.setattr(
        uploads_module,
        "extract_decisions_and_actions",
        lambda transcript_text: ExtractionResult(decisions=["d"], summary="s"),
    )
    _extract(client, project_id, cycle_id, upload_id, token)

    listing = client.get(
        f"/projects/{project_id}/cycles/{cycle_id}/uploads",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert listing[0]["status"] == "complete"
    assert listing[0]["transcript_text"] == "Transcript."


def test_real_extract_decisions_and_actions_raises_not_implemented_error() -> None:
    from app.ai_extraction import extract_decisions_and_actions

    with pytest.raises(NotImplementedError):
        extract_decisions_and_actions("some transcript")
