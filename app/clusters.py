from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Cluster, CycleStatus, FeedbackCard, FeedbackCycle, User
from app.security import require_project_member

router = APIRouter(prefix="/projects", tags=["clusters"])


class ClusterCreateRequest(BaseModel):
    name: str | None = None


class ClusterRenameRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("name must not be empty or whitespace")
        return value


class ClusterResponse(BaseModel):
    id: int
    cycle_id: int
    name: str | None

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


def _get_cluster(db: Session, cycle_id: int, cluster_id: int) -> Cluster:
    cluster = (
        db.query(Cluster)
        .filter(Cluster.id == cluster_id, Cluster.cycle_id == cycle_id)
        .first()
    )
    if cluster is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")
    return cluster


@router.post(
    "/{project_id}/cycles/{cycle_id}/clusters",
    response_model=ClusterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_cluster(
    project_id: int,
    cycle_id: int,
    payload: ClusterCreateRequest,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> Cluster:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status != CycleStatus.REVEALED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Clusters can only be created on a revealed cycle",
        )

    cluster = Cluster(cycle_id=cycle_id, name=payload.name)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


@router.get(
    "/{project_id}/cycles/{cycle_id}/clusters",
    response_model=list[ClusterResponse],
)
def list_clusters(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> list[Cluster]:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status == CycleStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Clusters are not visible until the cycle has been revealed",
        )

    return db.query(Cluster).filter(Cluster.cycle_id == cycle_id).all()


@router.patch(
    "/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}",
    response_model=ClusterResponse,
)
def rename_cluster(
    project_id: int,
    cycle_id: int,
    cluster_id: int,
    payload: ClusterRenameRequest,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> Cluster:
    cycle = _get_cycle(db, project_id, cycle_id)
    cluster = _get_cluster(db, cycle_id, cluster_id)

    if cycle.status != CycleStatus.REVEALED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Clusters can only be renamed on a revealed cycle",
        )

    cluster.name = payload.name
    db.commit()
    db.refresh(cluster)
    return cluster


@router.post(
    "/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}",
    response_model=ClusterResponse,
)
def merge_clusters(
    project_id: int,
    cycle_id: int,
    source_id: int,
    target_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> Cluster:
    cycle = _get_cycle(db, project_id, cycle_id)
    source = _get_cluster(db, cycle_id, source_id)
    target = _get_cluster(db, cycle_id, target_id)

    if source.id == target.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot merge a cluster into itself",
        )

    if cycle.status != CycleStatus.REVEALED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Clusters can only be merged on a revealed cycle",
        )

    # Reassign the cards and commit *before* deleting the source cluster.
    # Doing both in the same flush is unsafe: Cluster.cards is a
    # back-populated relationship, and SQLAlchemy's default (non-cascading)
    # delete behaviour nulls out a related row's foreign key for any child
    # still associated with the parent being deleted at flush time --
    # which would silently overwrite the `cluster_id = target.id` update
    # above with NULL. Committing the reassignment first removes the cards
    # from `source`'s association before the delete is even flushed.
    cards_in_source = db.query(FeedbackCard).filter(FeedbackCard.cluster_id == source.id).all()
    for card in cards_in_source:
        card.cluster_id = target.id
    db.commit()

    db.delete(source)
    db.commit()
    db.refresh(target)
    return target
