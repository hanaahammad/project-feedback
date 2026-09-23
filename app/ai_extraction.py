"""AI extraction of decisions/action items/summary from a transcript.

This module exists to give the rest of the app (specifically the
`POST /projects/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/extract`
endpoint in `app/uploads.py`) a single, mockable seam for calling out to an
AI/LLM provider to turn a completed transcript into draft `Decision` rows,
draft `ActionItem` rows, and a cycle-level summary.

Per #20's Out of scope: which provider to call, prompt design, and API
key/credential management are deliberately not addressed here, mirroring
#12's `app/ai_clustering.py`/`generate_cluster_suggestions` and #19's
`app/transcription.py`/`transcribe_audio` pattern exactly. No AI/LLM SDK is
added to `pyproject.toml` -- `extract_decisions_and_actions` is the seam a
future task wires a real provider in behind. Until then it raises
`NotImplementedError`, which the caller (the extract endpoint) catches and
turns into `status: "unavailable"`, so the absence of a configured provider
behaves the same as a failed/timed-out call, never as a request error, and
requires no real network call or credential in tests or in CI.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class ExtractedActionItem:
    """One AI-extracted action item, destined to become an unconfirmed
    #16 `ActionItem` row.

    `owner_email`, when present, is resolved by the caller to an `owner_id`
    via an exact `User.email` match plus a `ProjectMembership` check on the
    project -- a best-effort match against free text, left `None` (on the
    resulting row) rather than rejected when it doesn't resolve. `due_date`
    is a real `date`, not a string to parse, to avoid format edge cases.
    """

    description: str
    owner_email: str | None = None
    due_date: date | None = None


@dataclass
class ExtractionResult:
    """The AI provider's output for one extraction call: zero or more
    decision strings, zero or more action items, and one cycle-level
    summary.
    """

    decisions: list[str] = field(default_factory=list)
    action_items: list[ExtractedActionItem] = field(default_factory=list)
    summary: str = ""


def extract_decisions_and_actions(transcript_text: str) -> ExtractionResult:
    """Ask an AI/LLM provider to extract decisions, action items, and a
    summary from `transcript_text`.

    No provider is wired up in this task (see the module docstring), so
    this raises `NotImplementedError` unconditionally. Callers must catch
    exceptions from this function (the extract endpoint does) and tests
    must monkeypatch/stub it rather than relying on -- or requiring -- a
    real implementation.
    """
    raise NotImplementedError(
        "No AI extraction provider is configured. Provider selection, "
        "prompt design, and credential management are out of scope for "
        "this task -- extract_decisions_and_actions is the extension "
        "point a future task implements behind."
    )
