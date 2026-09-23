# Project Feedback

A FastAPI backend for running team retrospectives: submit Start/Stop/Continue
feedback, reveal and cluster it, vote on discussion topics, run the live
meeting (notes/decisions/action items), and optionally attach a meeting
recording for AI-assisted transcription and extraction. There is no
frontend/UI in this repo — everything is an API, exercised directly or via
the interactive docs described below.

## Setup

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Running the app

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

By default this uses a local SQLite file (`app.db`, created next to the
repo root). To point at a different database, set `DATABASE_URL` before
running either command, e.g.:

```bash
DATABASE_URL=postgresql://user:pass@localhost/project_feedback uv run alembic upgrade head
```

Once the server is running, open **http://127.0.0.1:8000/docs** for the
interactive Swagger UI — every endpoint can be called from there.

## Testing

Automated test suite (in-memory SQLite, no server needed):

```bash
uv run pytest            # whole suite
uv run pytest tests/test_home.py   # one file
```

### Manual smoke test via `/docs`

1. `POST /auth/signup`, then `POST /auth/login` to get an `access_token`.
   Click **Authorize** in Swagger UI and paste the token in as a bearer
   token to use it on every subsequent call.
2. `POST /projects` — create a project (you become its facilitator).
3. `POST /projects/{id}/cycles` — open a feedback cycle.
   `POST /projects/{id}/members` — invite teammates by email (they must
   have signed up already).
4. `POST /projects/{id}/cycles/{cycle_id}/cards` — submit Start/Stop/Continue
   feedback (as each member).
5. `POST .../reveal` — reveal all cards to the team.
6. `POST .../clusters` and `PATCH .../cards/{card_id}/cluster` — group
   cards into discussion topics.
7. `POST .../votes` — vote on topics; `POST .../close-voting` to lock it.
8. `PATCH .../clusters/{cluster_id}/discussion-status`,
   `POST .../notes` / `.../decisions` / `.../action-items` — run the
   live discussion.
9. `POST .../close` — close the cycle.
10. `POST .../uploads` (multipart form) — attach a recording or pasted
    transcript. `POST .../uploads/{id}/transcribe` and
    `.../extract` drive the AI pipeline — note that transcription
    (`app/transcription.py`) and extraction (`app/ai_extraction.py`) are
    unimplemented stubs (`NotImplementedError`) until a real provider is
    wired in, so these calls will report `status: "failed"` /
    `"unavailable"` out of the box.
11. `GET .../drafts` and `PATCH`/`DELETE` on a draft decision/action
    item/summary — facilitator reviews and confirms AI output.
12. `GET .../summary` — read-only retrospective summary for a closed cycle.
    `GET /projects/{id}/dashboard` — project-level landing view.

## Project layout

- `app/` — FastAPI routers (one file per resource area), models
  (`app/models.py`), auth/permissions (`app/security.py`)
- `migrations/` — Alembic migrations
- `tests/` — pytest suite, one file per router/feature
- `docs/tasks.md` — the backlog each feature was built against (goal,
  acceptance criteria, constraints per task)

## Notes

- `AGENTS.md` references `docs/testing-guidelines.md` and
  `docs/design-system.md` — neither currently exists in `docs/`.
- Dependencies are managed in `pyproject.toml`; see `AGENTS.md` for the
  project's rule on adding new ones.
