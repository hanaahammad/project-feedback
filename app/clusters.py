from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Literal

from .db import get_db
from .models import Cluster, Cycle, DiscussionStatusEnum
from .security import require_role, get_current_user
from .serializers import ClusterDiscussionStatusResponse

router = APIRouter()

@router.patch(
    "/projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/discussion-status",
    response_model=ClusterDiscussionStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def set_cluster_discussion_status(
    *,
    project_id: int,
    cycle_id: int,
    cluster_id: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user=Depends(require_role("facilitator")),
):
    """
    Update the discussion status of a cluster within a revealed cycle.
    """
    # Validate status
    status_value: str = body.get("status")
    if status_value not in {
        DiscussionStatusEnum.discussed.value,
        DiscussionStatusEnum.skipped.value,
        DiscussionStatusEnum.deferred.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid status. Must be one of 'discussed', 'skipped', or 'deferred'.",
        )

    # Fetch cluster ensuring it belongs to the cycle and project
    cluster = (
        db.query(Cluster)
        .join(Cycle, Cluster.cycle_id == Cycle.id)
        .filter(
            Cluster.id == cluster_id,
            Cycle.id == cycle_id,
            Cycle.project_id == project_id,
        )
        .first()
    )
    if not cluster:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")

    # Ensure cycle is in revealed state
    if cluster.cycle.status in ("open", "closed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot set discussion status on a cycle that is not revealed",
        )

    # Update status
    cluster.discussion_status = status_value
    db.commit()
    db.refresh(cluster)

    return ClusterDiscussionStatusResponse(
        cluster_id=cluster.id,
        cycle_id=cluster.cycle_id,
        status=cluster.discussion_status,
    )
