"""Shared serialization helpers for API responses.

Endpoints across routers (cards, and future clustering/reveal endpoints)
return data derived from ORM models. Serialization logic that needs to be
applied consistently everywhere a model is exposed -- such as the
anonymity rule for `FeedbackCard` -- lives here so every endpoint can
import and reuse it instead of re-deriving its own model-to-response
logic.
"""

from app.models import FeedbackCard


def serialize_feedback_card(card: FeedbackCard) -> dict:
    """Serialize a `FeedbackCard` for API responses.

    Every endpoint that returns `FeedbackCard` data must build its response
    from this function so the anonymity rule is enforced the same way
    everywhere: a card with `is_anonymous = True` never exposes any
    author-identifying field (no `author_id`, no nested author info) in
    its output, regardless of the caller -- including a facilitator. A
    card with `is_anonymous = False` includes the author's identity.
    """
    data = {
        "id": card.id,
        "cycle_id": card.cycle_id,
        "cluster_id": card.cluster_id,
        "category": card.category,
        "text": card.text,
        "is_anonymous": card.is_anonymous,
    }
    if not card.is_anonymous:
        data["author_id"] = card.author_id
    return data
