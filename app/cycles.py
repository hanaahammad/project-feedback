from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CycleStatus, FeedbackCycle, ProjectMembership, Role, User
from app.security import get_current_user, require_role

router = APIRouter(prefix="/projects", tags=["cycles"])


class CycleResponse(BaseModel):
    id: int
    project_id: int
    status: CycleStatus

    model_config = {"from_attributes": True}


class MemberAddRequest(BaseModel):
    email: str


class MemberResponse(BaseModel):
    user_id: int
    project_id: int
    role: str


@router.post("/{project_id}/cycles", response_model=CycleResponse, status_code=status.HTTP_201_CREATED)
def create_cycle(
    project_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> FeedbackCycle:
    cycle = FeedbackCycle(project_id=project_id)
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


@router.get("/{project_id}/cycles/{cycle_id}", response_model=CycleResponse)
def get_cycle(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FeedbackCycle:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")
    return cycle


@router.post("/{project_id}/cycles/{cycle_id}/reveal", response_model=CycleResponse)
def reveal_cycle(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> FeedbackCycle:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")

    if cycle.status != CycleStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cycle must be open to be revealed",
        )

    cycle.status = CycleStatus.REVEALED
    db.commit()
    db.refresh(cycle)
    return cycle


@router.post("/{project_id}/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
def add_member(
    project_id: int,
    payload: MemberAddRequest,
    current_user: User = Depends(require_role("facilitator")),
    db: Session = Depends(get_db),
) -> MemberResponse:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing_membership = (
        db.query(ProjectMembership)
        .filter(
            ProjectMembership.user_id == user.id,
            ProjectMembership.project_id == project_id,
        )
        .first()
    )
    if existing_membership is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this project",
        )

    team_member_role = db.query(Role).filter(Role.name == "team_member").first()
    if team_member_role is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Team member role is not configured",
        )

    membership = ProjectMembership(
        user_id=user.id,
        project_id=project_id,
        role_id=team_member_role.id,
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return MemberResponse(
        user_id=membership.user_id,
        project_id=membership.project_id,
        role=team_member_role.name,
    )
