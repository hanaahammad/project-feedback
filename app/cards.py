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


class CardEditRequest(BaseModel):
    category: CardCategory
    text: str
    is_anonymous: bool

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("text must not be empty or whitespace")
        return value


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


@router.get(
    "/{project_id}/cycles/{cycle_id}/cards/mine",
    response_model=list[dict],
)
def list_my_cards(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")

    # The "only my cards" filter is applied at the query level (WHERE
    # author_id = current_user.id) so another member's card is never
    # fetched from the database in the first place, let alone present in
    # the response body -- this holds regardless of cycle status.
    cards = (
        db.query(FeedbackCard)
        .filter(FeedbackCard.cycle_id == cycle_id, FeedbackCard.author_id == current_user.id)
        .all()
    )

    # response_model=list[dict] (rather than a fixed Pydantic schema) is
    # deliberate: the shared serializer omits the author_id key entirely
    # for an anonymous card rather than nulling it, and a declared
    # Pydantic field would re-introduce it as `null`. Using dict here
    # keeps this endpoint a pure pass-through of #8's serializer output,
    # with no ad-hoc re-derivation of which fields to hide.
    return [serialize_feedback_card(card) for card in cards]


@router.get(
    "/{project_id}/cycles/{cycle_id}/cards",
    response_model=list[dict],
)
def list_all_cards(
    project_id: int,
    cycle_id: int,
    current_user: User = Depends(require_project_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    cycle = (
        db.query(FeedbackCycle)
        .filter(FeedbackCycle.id == cycle_id, FeedbackCycle.project_id == project_id)
        .first()
    )
    if cycle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cycle not found")

    if cycle.status == CycleStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cards are not visible until the cycle has been revealed",
        )

    # Unlike /mine, no author_id filter here -- every card in the cycle is
    # returned regardless of who submitted it. Each card is still routed
    # through the shared serializer, so an anonymous card's author stays
    # hidden even from a facilitator viewing this response.
    cards = db.query(FeedbackCard).filter(FeedbackCard.cycle_id == cycle_id).all()

    return [serialize_feedback_card(card) for card in cards]


@router.put(
    "/{project_id}/cycles/{cycle_id}/cards/{card_id}",
    response_model=dict,
)
def update_card(
    project_id: int,
    cycle_id: int,
    card_id: int,
    payload: CardEditRequest,
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

    # Look up the card scoped to this cycle *and* to the current user's
    # authorship in a single query. A card that exists but belongs to a
    # different cycle/project, or that belongs to a different author,
    # comes back as None either way -- so both cases yield an identical
    # 404, never revealing that a card with this id exists and belongs
    # to someone else (true even for a facilitator).
    card = (
        db.query(FeedbackCard)
        .filter(
            FeedbackCard.id == card_id,
            FeedbackCard.cycle_id == cycle_id,
            FeedbackCard.author_id == current_user.id,
        )
        .first()
    )
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")

    if cycle.status != CycleStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cycle is not open for edits",
        )

    card.category = payload.category
    card.text = payload.text
    card.is_anonymous = payload.is_anonymous
    db.commit()
    db.refresh(card)

    return serialize_feedback_card(card)
