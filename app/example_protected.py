"""Scaffolding router used to prove `require_role` end-to-end (issue #4).

This is not a product feature. It exists solely so the `require_role`
dependency has one real route to guard, and so tests can exercise 401/403/200
without depending on any real feature route. Safe to delete once a real
facilitator-only route (e.g. #6, #10) exists and is tested the same way.
"""

from fastapi import APIRouter, Depends

from app.models import User
from app.security import require_role

router = APIRouter(tags=["example"])


@router.get("/projects/{project_id}/facilitator-ping")
def facilitator_ping(
    project_id: int,
    current_user: User = Depends(require_role("facilitator")),
) -> dict[str, str]:
    return {"status": "ok"}
