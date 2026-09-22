from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CardCategory, CycleStatus, FeedbackCard, FeedbackCycle, User
from app.security import require_project_member
from app.serializers import serialize_feedback_card

router = APIRouter(prefix="/projects", tags=["cards"])


class CardCreateRequest(BaseModel):
    category: CardCategory
    text: str
    is_anonymous: bool = False

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("text must not be empty or whitespace")
        return value


class CardResponse(BaseModel):
    id: int
    cycle_id: int
    category: CardCategory
    text: str

    model_config = {"from_attributes": True}


@router.post(
    "/{project_id}/cycles/{cycle_id}/cards",
    response_model=CardResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_card(
    project_id: int,
    cycle_id: int,
    payload: CardCreateRequest,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> dict:
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
            detail="Cycle is not open for submissions",
        )

    card = FeedbackCard(
        cycle_id=cycle_id,
        category=payload.category,
        text=payload.text,
        author_id=current_user.id,
        is_anonymous=payload.is_anonymous,
    )
    db.add(card)
    db.commit()
    db.refresh(card)

    # Route the response through the shared serializer so the anonymity
    # rule is applied consistently, then let CardResponse's declared
    # fields (id, cycle_id, category, text) filter it down -- this
    # endpoint's response shape has never included an author field, for
    # either an anonymous or a non-anonymous card.
    return serialize_feedback_card(card)
