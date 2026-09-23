import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai_extraction import extract_decisions_and_actions
from app.db import get_db
from app.models import (
    ActionItem,
    ActionStatus,
    CycleStatus,
    Decision,
    FeedbackCycle,
    MeetingUpload,
    ProjectMembership,
    UploadKind,
    UploadStatus,
    User,
)
from app.security import require_project_member, require_role
from app.transcription import transcribe_audio

router = APIRouter(prefix="/projects", tags=["uploads"])

UPLOADS_DIR = Path("uploads")

FILE_KINDS = {UploadKind.AUDIO, UploadKind.VIDEO, UploadKind.TRANSCRIPT_FILE}


class MeetingUploadResponse(BaseModel):
    id: int
    cycle_id: int
    kind: UploadKind
    status: UploadStatus
    transcript_text: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _get_cycle(db: Session, project_id: int, cycle_id: int) -> FeedbackCycle:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return cycle


def _get_upload(db: Session, cycle_id: int, upload_id: int) -> MeetingUpload:
    upload = (
        db.query(MeetingUpload)
        .filter(MeetingUpload.id == upload_id, MeetingUpload.cycle_id == cycle_id)
        .first()
    )
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return upload


def _resolve_owner_id(db: Session, project_id: int, owner_email: str | None) -> int | None:
    """Best-effort match of an extracted `owner_email` to an existing
    project member's user id. Returns `None` (rather than raising) when
    there is no matching `User`, or a matching `User` with no
    `ProjectMembership` on this project -- an AI-drafted guess is not
    validated the way a human-entered owner_id is (#16).
    """
    if not owner_email:
        return None
    user = db.query(User).filter(User.email == owner_email).first()
    if user is None:
        return None
    membership = (
        db.query(ProjectMembership)
        .filter(ProjectMembership.user_id == user.id, ProjectMembership.project_id == project_id)
        .first()
    )
    if membership is None:
        return None
    return user.id


class DecisionDraftResponse(BaseModel):
    id: int
    cycle_id: int
    cluster_id: int | None
    description: str
    confirmed: bool
    author_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ActionItemDraftResponse(BaseModel):
    id: int
    cycle_id: int
    cluster_id: int | None
    description: str
    confirmed: bool
    owner_id: int | None
    due_date: date | None
    status: ActionStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class ExtractResponse(BaseModel):
    status: str
    summary: str | None
    decisions: list[DecisionDraftResponse]
    action_items: list[ActionItemDraftResponse]


@router.post(
    "/{project_id}/cycles/{cycle_id}/uploads",
    response_model=MeetingUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload(
    project_id: int,
    cycle_id: int,
    kind: str = Form(...),
    transcript_text: str | None = Form(None),
    file: UploadFile | None = None,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> MeetingUpload:
    cycle = _get_cycle(db, project_id, cycle_id)

    try:
        upload_kind = UploadKind(kind)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid kind")

    if cycle.status != CycleStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Uploads can only be attached to a closed cycle",
        )

    file_bytes = await file.read() if file is not None else b""
    has_file = file is not None and len(file_bytes) > 0
    has_transcript_text = transcript_text is not None and transcript_text.strip() != ""

    if has_file and has_transcript_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either a file or transcript_text, not both",
        )

    if upload_kind in FILE_KINDS:
        if not has_file:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="file is required")
    else:
        if not has_transcript_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="transcript_text is required"
            )

    upload = MeetingUpload(cycle_id=cycle_id, kind=upload_kind, status=UploadStatus.PENDING)

    if upload_kind in FILE_KINDS:
        cycle_dir = UPLOADS_DIR / str(cycle_id)
        cycle_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4()}_{file.filename}"
        destination = cycle_dir / stored_name
        destination.write_bytes(file_bytes)
        upload.file_path = str(destination)
    else:
        upload.transcript_text = transcript_text

    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


@router.get("/{project_id}/cycles/{cycle_id}/uploads", response_model=list[MeetingUploadResponse])
def list_uploads(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> list[MeetingUpload]:
    _get_cycle(db, project_id, cycle_id)

    return (
        db.query(MeetingUpload)
        .filter(MeetingUpload.cycle_id == cycle_id)
        .order_by(MeetingUpload.created_at.asc())
        .all()
    )


@router.post(
    "/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/transcribe",
    response_model=MeetingUploadResponse,
)
def transcribe_upload(
    project_id: int,
    cycle_id: int,
    upload_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> MeetingUpload:
    _get_cycle(db, project_id, cycle_id)
    upload = _get_upload(db, cycle_id, upload_id)

    if upload.kind not in {UploadKind.AUDIO, UploadKind.VIDEO}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only audio/video uploads can be transcribed",
        )

    if upload.status != UploadStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload must be pending to be transcribed",
        )

    upload.status = UploadStatus.PROCESSING
    db.commit()

    try:
        transcript_text = transcribe_audio(upload.file_path)
    except Exception:
        upload.status = UploadStatus.FAILED
        db.commit()
        db.refresh(upload)
        return upload

    upload.status = UploadStatus.COMPLETE
    upload.transcript_text = transcript_text
    db.commit()
    db.refresh(upload)
    return upload


@router.post(
    "/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/extract",
    response_model=ExtractResponse,
)
def extract_upload(
    project_id: int,
    cycle_id: int,
    upload_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)
    upload = _get_upload(db, cycle_id, upload_id)

    if upload.status != UploadStatus.COMPLETE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload must be complete to extract decisions and action items",
        )

    try:
        result = extract_decisions_and_actions(upload.transcript_text)
    except Exception:
        return {"status": "unavailable", "summary": None, "decisions": [], "action_items": []}

    new_decisions = []
    for text in result.decisions:
        if not text or not text.strip():
            continue
        decision = Decision(
            cycle_id=cycle_id,
            cluster_id=None,
            author_id=None,
            description=text,
            confirmed=False,
        )
        db.add(decision)
        new_decisions.append(decision)

    new_action_items = []
    for item in result.action_items:
        if not item.description or not item.description.strip():
            continue
        action_item = ActionItem(
            cycle_id=cycle_id,
            cluster_id=None,
            owner_id=_resolve_owner_id(db, project_id, item.owner_email),
            description=item.description,
            due_date=item.due_date,
            status=ActionStatus.OPEN,
            confirmed=False,
        )
        db.add(action_item)
        new_action_items.append(action_item)

    cycle.ai_summary = result.summary
    db.commit()

    for decision in new_decisions:
        db.refresh(decision)
    for action_item in new_action_items:
        db.refresh(action_item)

    return {
        "status": "extracted",
        "summary": result.summary,
        "decisions": new_decisions,
        "action_items": new_action_items,
    }
