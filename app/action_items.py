from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ActionItem, ActionStatus, Cluster, FeedbackCycle, User
from app.security import require_project_member

router = APIRouter(prefix="/projects", tags=["action-items"])


class ActionItemListResponse(BaseModel):
    id: int
    cycle_id: int
    cluster_id: int | None
    cluster_name: str | None
    description: str
    owner_id: int | None
    due_date: date | None
    status: ActionStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class ActionItemStatusUpdateRequest(BaseModel):
    status: ActionStatus


def _get_project_action_item(db: Session, project_id: int, action_item_id: int) -> ActionItem:
    action_item = (
        db.query(ActionItem)
        .join(FeedbackCycle, ActionItem.cycle_id == FeedbackCycle.id)
        .filter(
            ActionItem.id == action_item_id,
            FeedbackCycle.project_id == project_id,
            ActionItem.confirmed == True,  # noqa: E712
        )
        .first()
    )
    if action_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action item not found")
    return action_item


@router.get("/{project_id}/action-items", response_model=list[ActionItemListResponse])
def list_action_items(
    project_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(ActionItem, Cluster.name)
        .join(FeedbackCycle, ActionItem.cycle_id == FeedbackCycle.id)
        .outerjoin(Cluster, ActionItem.cluster_id == Cluster.id)
        .filter(
            FeedbackCycle.project_id == project_id,
            ActionItem.confirmed == True,  # noqa: E712
        )
        .order_by(ActionItem.created_at.asc())
        .all()
    )

    return [
        {
            "id": action_item.id,
            "cycle_id": action_item.cycle_id,
            "cluster_id": action_item.cluster_id,
            "cluster_name": cluster_name,
            "description": action_item.description,
            "owner_id": action_item.owner_id,
            "due_date": action_item.due_date,
            "status": action_item.status,
            "created_at": action_item.created_at,
        }
        for action_item, cluster_name in rows
    ]


@router.patch("/{project_id}/action-items/{action_item_id}", response_model=ActionItemListResponse)
def update_action_item_status(
    project_id: int,
    action_item_id: int,
    payload: ActionItemStatusUpdateRequest,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> dict:
    action_item = _get_project_action_item(db, project_id, action_item_id)

    if action_item.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned owner can update this action item's status",
        )

    action_item.status = payload.status
    db.commit()
    db.refresh(action_item)

    cluster_name = None
    if action_item.cluster_id is not None:
        cluster_name = db.query(Cluster.name).filter(Cluster.id == action_item.cluster_id).scalar()

    return {
        "id": action_item.id,
        "cycle_id": action_item.cycle_id,
        "cluster_id": action_item.cluster_id,
        "cluster_name": cluster_name,
        "description": action_item.description,
        "owner_id": action_item.owner_id,
        "due_date": action_item.due_date,
        "status": action_item.status,
        "created_at": action_item.created_at,
    }
