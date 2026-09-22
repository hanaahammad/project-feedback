from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Cluster, CycleStatus, FeedbackCycle, ProjectMembership, User, Vote
from app.security import require_project_member, require_role

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


class CloseVotingResponse(BaseModel):
    cycle_id: int
    project_id: int
    voting_closed: bool


class ClusterVoteResult(BaseModel):
    cluster_id: int
    name: str | None
    vote_count: int


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


@router.post(
    "/{project_id}/cycles/{cycle_id}/close-voting",
    response_model=CloseVotingResponse,
)
def close_voting(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status != CycleStatus.REVEALED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Voting can only be closed on a revealed cycle",
        )

    if cycle.voting_closed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Voting is already closed for this cycle",
        )

    cycle.voting_closed = True
    db.commit()
    db.refresh(cycle)

    return {"cycle_id": cycle_id, "project_id": project_id, "voting_closed": cycle.voting_closed}


def _everyone_has_voted(db: Session, project_id: int, cycle_id: int) -> bool:
    """Compares the distinct count of project members against the distinct
    count of participants who have cast at least one non-empty ballot
    (i.e. at least one `Vote` row) on a cluster in this cycle.

    Computed lazily/live on every call -- deliberately not cached or
    persisted, per #14's Constraints. A participant whose most recent
    submission was an empty `cluster_ids: []` ballot has zero `Vote` rows
    and is therefore not counted as having voted, even though they did
    submit -- this is accepted behaviour, not a bug (see #14's grooming
    notes on abstention).
    """
    member_count = (
        db.query(ProjectMembership.user_id)
        .filter(ProjectMembership.project_id == project_id)
        .distinct()
        .count()
    )
    voted_count = (
        db.query(Vote.participant_id)
        .join(Cluster, Vote.cluster_id == Cluster.id)
        .filter(Cluster.cycle_id == cycle_id)
        .distinct()
        .count()
    )
    return member_count > 0 and voted_count >= member_count


@router.get(
    "/{project_id}/cycles/{cycle_id}/votes/results",
    response_model=list[ClusterVoteResult],
)
def vote_results(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status == CycleStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Voting results are not available until the cycle has been revealed",
        )

    if not cycle.voting_closed and not _everyone_has_voted(db, project_id, cycle_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="voting results are not available yet",
        )

    vote_count_subquery = (
        db.query(Vote.cluster_id, func.count(Vote.id).label("vote_count"))
        .join(Cluster, Vote.cluster_id == Cluster.id)
        .filter(Cluster.cycle_id == cycle_id)
        .group_by(Vote.cluster_id)
        .subquery()
    )

    vote_count_column = func.coalesce(vote_count_subquery.c.vote_count, 0)

    rows = (
        db.query(Cluster.id, Cluster.name, vote_count_column.label("vote_count"))
        .outerjoin(vote_count_subquery, Cluster.id == vote_count_subquery.c.cluster_id)
        .filter(Cluster.cycle_id == cycle_id)
        .order_by(vote_count_column.desc(), Cluster.id.asc())
        .all()
    )

    return [{"cluster_id": row.id, "name": row.name, "vote_count": row.vote_count} for row in rows]
