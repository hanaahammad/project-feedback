"""AI-assisted clustering suggestion extension point.

This module exists to give the rest of the app (specifically the
`POST /projects/{project_id}/cycles/{cycle_id}/suggest-clusters` endpoint
in `app/clusters.py`) a single, mockable seam for calling out to an AI/LLM
provider to group a revealed cycle's still-ungrouped feedback cards.

Per #12's Out of scope: which provider to call, prompt design, and
API key/credential management are deliberately not addressed here. No
AI/LLM SDK is added to `pyproject.toml` -- `generate_cluster_suggestions`
is the seam a future task wires a real provider in behind. Until then it
raises `NotImplementedError`, which the caller (the suggest-clusters
endpoint) catches like any other failure and turns into a `200`
`{"status": "unavailable", "clusters": []}` response -- so the absence of
a configured provider behaves the same as a failed/timed-out call, never
as an error, and requires no real network call or credential in tests or
in CI.
"""

from dataclasses import dataclass, field

from app.models import FeedbackCard


@dataclass
class SuggestedGroup:
    """One AI-suggested grouping of cards, destined to become an ordinary
    #11 `Cluster` plus the reassignment of each listed card's `cluster_id`
    to that new cluster.

    `name` is optional, matching #11's `Cluster.name` being nullable --
    an AI suggestion is not required to propose a label for every group.
    `card_ids` holds the `FeedbackCard.id` values (drawn only from the
    ungrouped cards passed into `generate_cluster_suggestions`) that
    belong in this group.
    """

    card_ids: list[int] = field(default_factory=list)
    name: str | None = None


def generate_cluster_suggestions(cards: list[FeedbackCard]) -> list[SuggestedGroup]:
    """Ask an AI/LLM provider to group `cards` into suggested clusters.

    `cards` is always the caller's current set of still-ungrouped cards
    (`cluster_id is None`) for a revealed cycle -- the caller is
    responsible for that filtering, not this function.

    No provider is wired up in this task (see the module docstring), so
    this raises `NotImplementedError` unconditionally. Callers must catch
    exceptions from this function (the suggest-clusters endpoint does) and
    tests must monkeypatch/stub it rather than relying on -- or requiring
    -- a real implementation.
    """
    raise NotImplementedError(
        "No AI clustering provider is configured. Provider selection, "
        "prompt design, and credential management are out of scope for "
        "this task -- generate_cluster_suggestions is the extension point "
        "a future task implements behind."
    )
