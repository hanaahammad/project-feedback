from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ActionItem, ActionStatus, Cluster, CycleStatus, FeedbackCard, FeedbackCycle, User
from app.security import require_project_member

router = APIRouter(prefix="/projects", tags=["dashboard"])


@router.get("/{project_id}/dashboard", response_model=dict)
def get_dashboard(
    project_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> dict:
    current_cycle_row = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.project_id == project_id, FeedbackCycle.status != CycleStatus.CLOSED)
        .order_by(FeedbackCycle.created_at.desc(), FeedbackCycle.id.desc())
        .first()
    )
    current_cycle = None
    if current_cycle_row is not None:
        submission_count = (
            db.query(FeedbackCard).filter(FeedbackCard.cycle_id == current_cycle_row.id).count()
        )
        current_cycle = {
            "id": current_cycle_row.id,
            "status": current_cycle_row.status,
            "created_at": current_cycle_row.created_at,
            "submission_count": submission_count,
        }

    previous_cycle_rows = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.project_id == project_id, FeedbackCycle.status == CycleStatus.CLOSED)
        .order_by(FeedbackCycle.created_at.desc(), FeedbackCycle.id.desc())
        .all()
    )
    previous_cycles = [
        {"id": cycle.id, "status": cycle.status, "created_at": cycle.created_at} for cycle in previous_cycle_rows
    ]

    action_item_rows = (
        db.query(ActionItem, Cluster.name)
        .join(FeedbackCycle, ActionItem.cycle_id == FeedbackCycle.id)
        .outerjoin(Cluster, ActionItem.cluster_id == Cluster.id)
        .filter(
            FeedbackCycle.project_id == project_id,
            ActionItem.confirmed == True,  # noqa: E712
            ActionItem.status == ActionStatus.OPEN,
        )
        .order_by(ActionItem.created_at.asc())
        .all()
    )
    open_action_items = [
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
        for action_item, cluster_name in action_item_rows
    ]

    return {
        "current_cycle": current_cycle,
        "previous_cycles": previous_cycles,
        "open_action_items": open_action_items,
    }
