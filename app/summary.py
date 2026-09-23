from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    ActionItem,
    Cluster,
    CycleStatus,
    Decision,
    DiscussionNote,
    FeedbackCard,
    FeedbackCycle,
    User,
    Vote,
)
from app.security import require_project_member
from app.serializers import serialize_feedback_card

router = APIRouter(prefix="/projects", tags=["summary"])


def _get_cycle(db: Session, project_id: int, cycle_id: int) -> FeedbackCycle:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return cycle


@router.get("/{project_id}/cycles/{cycle_id}/summary", response_model=dict)
def get_cycle_summary(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> dict:
    cycle = _get_cycle(db, project_id, cycle_id)

    if cycle.status != CycleStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The cycle summary is only available once the cycle is closed",
        )

    # Ranking is computed live from Vote rows, independent of #14's
    # voting_closed/everyone-voted gate -- this endpoint only exists once
    # the cycle is closed, by which point voting is over regardless of
    # whether close-voting was ever explicitly called.
    vote_count_subquery = (
        db.query(Vote.cluster_id, func.count(Vote.id).label("vote_count"))
        .join(Cluster, Vote.cluster_id == Cluster.id)
        .filter(Cluster.cycle_id == cycle_id)
        .group_by(Vote.cluster_id)
        .subquery()
    )
    vote_count_column = func.coalesce(vote_count_subquery.c.vote_count, 0)
    topic_rows = (
        db.query(Cluster.id, Cluster.name, Cluster.discussion_status, vote_count_column.label("vote_count"))
        .outerjoin(vote_count_subquery, Cluster.id == vote_count_subquery.c.cluster_id)
        .filter(Cluster.cycle_id == cycle_id)
        .order_by(vote_count_column.desc(), Cluster.id.asc())
        .all()
    )
    top_discussion_topics = [
        {
            "cluster_id": row.id,
            "name": row.name,
            "vote_count": row.vote_count,
            "discussion_status": row.discussion_status,
        }
        for row in topic_rows
    ]

    notes = (
        db.query(DiscussionNote)
        .join(Cluster, DiscussionNote.cluster_id == Cluster.id)
        .filter(Cluster.cycle_id == cycle_id)
        .order_by(DiscussionNote.created_at.asc())
        .all()
    )
    notes_out = [
        {
            "id": note.id,
            "cycle_id": cycle_id,
            "cluster_id": note.cluster_id,
            "text": note.text,
            "author_id": note.author_id,
            "created_at": note.created_at,
        }
        for note in notes
    ]

    decisions = (
        db.query(Decision)
        .filter(Decision.cycle_id == cycle_id, Decision.confirmed == True)  # noqa: E712
        .order_by(Decision.created_at.asc())
        .all()
    )
    confirmed_decisions = [
        {
            "id": decision.id,
            "cycle_id": decision.cycle_id,
            "cluster_id": decision.cluster_id,
            "description": decision.description,
            "author_id": decision.author_id,
            "confirmed": decision.confirmed,
            "created_at": decision.created_at,
        }
        for decision in decisions
    ]

    action_items = (
        db.query(ActionItem)
        .filter(ActionItem.cycle_id == cycle_id, ActionItem.confirmed == True)  # noqa: E712
        .order_by(ActionItem.created_at.asc())
        .all()
    )
    confirmed_action_items = [
        {
            "id": action_item.id,
            "cycle_id": action_item.cycle_id,
            "cluster_id": action_item.cluster_id,
            "description": action_item.description,
            "owner_id": action_item.owner_id,
            "due_date": action_item.due_date,
            "status": action_item.status,
            "confirmed": action_item.confirmed,
            "created_at": action_item.created_at,
        }
        for action_item in action_items
    ]

    card_author_ids = {
        row[0]
        for row in db.query(FeedbackCard.author_id).filter(FeedbackCard.cycle_id == cycle_id).distinct()
    }
    voter_ids = {
        row[0]
        for row in db.query(Vote.participant_id)
        .join(Cluster, Vote.cluster_id == Cluster.id)
        .filter(Cluster.cycle_id == cycle_id)
        .distinct()
    }
    attendance = sorted(card_author_ids | voter_ids)

    cards = (
        db.query(FeedbackCard)
        .filter(FeedbackCard.cycle_id == cycle_id)
        .order_by(FeedbackCard.created_at.asc())
        .all()
    )
    original_cards = [serialize_feedback_card(card) for card in cards]

    summary = cycle.ai_summary if cycle.summary_confirmed else None

    return {
        "top_discussion_topics": top_discussion_topics,
        "notes": notes_out,
        "confirmed_decisions": confirmed_decisions,
        "confirmed_action_items": confirmed_action_items,
        "attendance": attendance,
        "original_cards": original_cards,
        "summary": summary,
    }
