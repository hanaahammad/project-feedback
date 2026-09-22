from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Cluster, CycleStatus, FeedbackCycle, User, Vote
from app.security import require_project_member

router = APIRouter(prefix="/projects", tags=["votes"])

MAX_VOTES_PER_SUBMISSION = 3


class VoteSubmitRequest(BaseModel):
    cluster_ids: list[int]

    @field_validator("cluster_ids")
    @classmethod
    def cluster_ids_must_not_exceed_max(cls, value: list[int]) -> list[int]:
        if len(value) > MAX_VOTES_PER_SUBMISSION:
            raise ValueError(f"cluster_ids must contain at most {MAX_VOTES_PER_SUBMISSION} entries")
        return value


class VoteAllocationResponse(BaseModel):
    cycle_id: int
    cluster_ids: list[int]


def _get_cycle(db: Session, project_id: int, cycle_id: int) -> FeedbackCycle:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return cycle


@router.post(
    "/{project_id}/cycles/{cycle_id}/votes",
    response_model=VoteAllocationResponse,
)
def cast_votes(
    project_id: int,
    cycle_id: int,
    payload: VoteSubmitRequest,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status != CycleStatus.REVEALED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Votes can only be cast on a revealed cycle",
        )

    # Validate every submitted cluster id belongs to this cycle *before*
    # writing anything -- an invalid/foreign id rejects the whole request
    # with no partial writes, even if earlier ids in the list were valid.
    if payload.cluster_ids:
        valid_ids_in_cycle = {
            row.id
            for row in db.query(Cluster.id).filter(
                Cluster.cycle_id == cycle_id,
                Cluster.id.in_(set(payload.cluster_ids)),
            )
        }
        for cluster_id in payload.cluster_ids:
            if cluster_id not in valid_ids_in_cycle:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Cluster {cluster_id} not found in this cycle",
                )

    # Replace-on-resubmit: delete the participant's prior votes for this
    # cycle (joined through Cluster, since Vote has no cycle_id of its own)
    # and insert the newly submitted allocation, atomically in one
    # transaction.
    db.query(Vote).filter(
        Vote.participant_id == current_user.id,
        Vote.cluster_id.in_(db.query(Cluster.id).filter(Cluster.cycle_id == cycle_id)),
    ).delete(synchronize_session=False)

    for cluster_id in payload.cluster_ids:
        db.add(Vote(cluster_id=cluster_id, participant_id=current_user.id))

    db.commit()

    return {"cycle_id": cycle_id, "cluster_ids": list(payload.cluster_ids)}


@router.get(
    "/{project_id}/cycles/{cycle_id}/votes/mine",
    response_model=VoteAllocationResponse,
)
def my_votes(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status == CycleStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Votes are not visible until the cycle has been revealed",
        )

    votes = (
        db.query(Vote)
        .join(Cluster, Vote.cluster_id == Cluster.id)
        .filter(Cluster.cycle_id == cycle_id, Vote.participant_id == current_user.id)
        .all()
    )

    return {"cycle_id": cycle_id, "cluster_ids": [vote.cluster_id for vote in votes]}
