from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.ai_clustering import generate_cluster_suggestions
from app.db import get_db
from app.models import Cluster, CycleStatus, DiscussionStatus, FeedbackCard, FeedbackCycle, User
from app.security import require_project_member, require_role

router = APIRouter(prefix="/projects", tags=["clusters"])

# The set of values a facilitator may explicitly set via the
# discussion-status endpoint. `pending` is deliberately excluded -- it's
# the default a cluster starts in, and a topic cannot be manually reset
# back to not-yet-addressed (see #15's acceptance criteria).
_SETTABLE_DISCUSSION_STATUSES = {
    DiscussionStatus.DISCUSSED.value,
    DiscussionStatus.SKIPPED.value,
    DiscussionStatus.DEFERRED.value,
}


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


class SuggestClustersResponse(BaseModel):
    status: str
    clusters: list[ClusterResponse]


class DiscussionStatusUpdateRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def status_must_be_settable(cls, value: str) -> str:
        if value not in _SETTABLE_DISCUSSION_STATUSES:
            raise ValueError(
                "status must be one of 'discussed', 'skipped', or 'deferred'"
            )
        return value


class DiscussionStatusResponse(BaseModel):
    cluster_id: int
    cycle_id: int
    status: DiscussionStatus


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


@router.patch(
    "/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/discussion-status",
    response_model=DiscussionStatusResponse,
)
def set_discussion_status(
    project_id: int,
    cycle_id: int,
    cluster_id: int,
    payload: DiscussionStatusUpdateRequest,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)
    cluster = _get_cluster(db, cycle_id, cluster_id)

    if cycle.status != CycleStatus.REVEALED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Discussion status can only be set on a revealed cycle",
        )

    cluster.discussion_status = DiscussionStatus(payload.status)
    db.commit()
    db.refresh(cluster)

    return {"cluster_id": cluster.id, "cycle_id": cycle_id, "status": cluster.discussion_status}


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


@router.post(
    "/{project_id}/cycles/{cycle_id}/suggest-clusters",
    response_model=SuggestClustersResponse,
)
def suggest_clusters(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> dict:
    """Trigger an AI-generated clustering suggestion for a revealed cycle's
    still-ungrouped cards.

    This is a separate, optional endpoint from #10's reveal action by
    design (see #12's Constraints): reveal must stay fast and succeed or
    fail purely on its own rules, regardless of an AI provider's
    availability or latency, so the AI call is never made from there.

    A suggestion is applied by creating ordinary #11 `Cluster` rows (the
    same shape #11's create-cluster endpoint uses) and reassigning each
    grouped card's `cluster_id` -- no new read/board endpoint is added;
    the result is visible through #11's `GET .../clusters` and #10's
    `GET .../cards` exactly like a manually created cluster.
    """
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status != CycleStatus.REVEALED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cluster suggestions can only be generated on a revealed cycle",
        )

    # Only cards with cluster_id is null at call time are eligible -- a
    # card already assigned (manually, or by a prior suggestion run) is
    # never passed to the AI function and is never touched below.
    ungrouped_cards = (
        db.query(FeedbackCard)
        .filter(FeedbackCard.cycle_id == cycle_id, FeedbackCard.cluster_id.is_(None))
        .all()
    )

    try:
        groups = generate_cluster_suggestions(ungrouped_cards)
    except Exception:
        # A failed/timed-out/unconfigured AI call is never an error to the
        # caller: no Cluster rows are created, no card's cluster_id
        # changes (nothing has been written yet), and the board still
        # loads via #10/#11's existing read endpoints.
        return {"status": "unavailable", "clusters": []}

    eligible_card_ids = {card.id for card in ungrouped_cards}
    created_clusters: list[Cluster] = []

    for group in groups:
        cluster = Cluster(cycle_id=cycle_id, name=group.name)
        db.add(cluster)
        db.flush()  # assign cluster.id without committing yet

        for card_id in group.card_ids:
            # Defensively ignore any id the AI function returns that
            # wasn't actually one of the ungrouped cards handed to it --
            # an already-clustered or unknown card is never reassigned.
            if card_id not in eligible_card_ids:
                continue
            card = next((c for c in ungrouped_cards if c.id == card_id), None)
            if card is not None:
                card.cluster_id = cluster.id

        created_clusters.append(cluster)

    db.commit()
    for cluster in created_clusters:
        db.refresh(cluster)

    return {"status": "applied", "clusters": created_clusters}
