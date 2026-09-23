from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ActionItem, Decision, FeedbackCycle, ProjectMembership, User
from app.security import require_role
from app.uploads import ActionItemDraftResponse, DecisionDraftResponse

router = APIRouter(prefix="/projects", tags=["review"])


class DraftsResponse(BaseModel):
    summary: str | None
    summary_confirmed: bool
    decisions: list[DecisionDraftResponse]
    action_items: list[ActionItemDraftResponse]


class DecisionUpdateRequest(BaseModel):
    description: str | None = None
    confirmed: bool | None = None

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("description must not be empty or whitespace")
        return value


class ActionItemUpdateRequest(BaseModel):
    description: str | None = None
    due_date: date | None = None
    owner_id: int | None = None
    confirmed: bool | None = None

    @field_validator("description")
    @classmethod
    def description_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("description must not be empty or whitespace")
        return value


class SummaryUpdateRequest(BaseModel):
    summary: str | None = None
    confirmed: bool | None = None


class SummaryResponse(BaseModel):
    summary: str | None
    summary_confirmed: bool


def _get_cycle(db: Session, project_id: int, cycle_id: int) -> FeedbackCycle:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return cycle


def _get_decision(db: Session, cycle_id: int, decision_id: int) -> Decision:
    decision = (
        db.query(Decision).filter(Decision.id == decision_id, Decision.cycle_id == cycle_id).first()
    )
    if decision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found")
    return decision


def _get_action_item(db: Session, cycle_id: int, action_item_id: int) -> ActionItem:
    action_item = (
        db.query(ActionItem)
        .filter(ActionItem.id == action_item_id, ActionItem.cycle_id == cycle_id)
        .first()
    )
    if action_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action item not found")
    return action_item


def _validate_owner_id(db: Session, project_id: int, owner_id: int) -> None:
    membership = (
        db.query(ProjectMembership)
        .filter(ProjectMembership.user_id == owner_id, ProjectMembership.project_id == project_id)
        .first()
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner not found")


@router.get("/{project_id}/cycles/{cycle_id}/drafts", response_model=DraftsResponse)
def list_drafts(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)

    decisions = (
        db.query(Decision)
        .filter(Decision.cycle_id == cycle_id, Decision.confirmed == False)  # noqa: E712
        .order_by(Decision.created_at.asc())
        .all()
    )
    action_items = (
        db.query(ActionItem)
        .filter(ActionItem.cycle_id == cycle_id, ActionItem.confirmed == False)  # noqa: E712
        .order_by(ActionItem.created_at.asc())
        .all()
    )

    return {
        "summary": cycle.ai_summary,
        "summary_confirmed": cycle.summary_confirmed,
        "decisions": decisions,
        "action_items": action_items,
    }


@router.patch("/{project_id}/cycles/{cycle_id}/decisions/{decision_id}", response_model=DecisionDraftResponse)
def update_decision(
    project_id: int,
    cycle_id: int,
    decision_id: int,
    payload: DecisionUpdateRequest,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> Decision:
    _get_cycle(db, project_id, cycle_id)
    decision = _get_decision(db, cycle_id, decision_id)

    if decision.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Decision is already confirmed")

    fields = payload.model_fields_set
    if "description" in fields:
        decision.description = payload.description
    if "confirmed" in fields and payload.confirmed:
        decision.confirmed = True

    db.commit()
    db.refresh(decision)
    return decision


@router.delete("/{project_id}/cycles/{cycle_id}/decisions/{decision_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_decision(
    project_id: int,
    cycle_id: int,
    decision_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> None:
    _get_cycle(db, project_id, cycle_id)
    decision = _get_decision(db, cycle_id, decision_id)

    if decision.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Decision is already confirmed")

    db.delete(decision)
    db.commit()


@router.patch(
    "/{project_id}/cycles/{cycle_id}/action-items/{action_item_id}", response_model=ActionItemDraftResponse
)
def update_action_item(
    project_id: int,
    cycle_id: int,
    action_item_id: int,
    payload: ActionItemUpdateRequest,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> ActionItem:
    _get_cycle(db, project_id, cycle_id)
    action_item = _get_action_item(db, cycle_id, action_item_id)

    if action_item.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Action item is already confirmed")

    fields = payload.model_fields_set
    if "owner_id" in fields and payload.owner_id is not None:
        _validate_owner_id(db, project_id, payload.owner_id)

    if "description" in fields:
        action_item.description = payload.description
    if "due_date" in fields:
        action_item.due_date = payload.due_date
    if "owner_id" in fields:
        action_item.owner_id = payload.owner_id
    if "confirmed" in fields and payload.confirmed:
        action_item.confirmed = True

    db.commit()
    db.refresh(action_item)
    return action_item


@router.delete(
    "/{project_id}/cycles/{cycle_id}/action-items/{action_item_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_action_item(
    project_id: int,
    cycle_id: int,
    action_item_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> None:
    _get_cycle(db, project_id, cycle_id)
    action_item = _get_action_item(db, cycle_id, action_item_id)

    if action_item.confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Action item is already confirmed")

    db.delete(action_item)
    db.commit()


@router.patch("/{project_id}/cycles/{cycle_id}/summary", response_model=SummaryResponse)
def update_summary(
    project_id: int,
    cycle_id: int,
    payload: SummaryUpdateRequest,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.summary_confirmed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Summary is already confirmed")

    fields = payload.model_fields_set
    if "summary" in fields:
        cycle.ai_summary = payload.summary
    if "confirmed" in fields and payload.confirmed:
        cycle.summary_confirmed = True

    db.commit()
    db.refresh(cycle)
    return {"summary": cycle.ai_summary, "summary_confirmed": cycle.summary_confirmed}
