# Backlog

## 1. Project scaffold with a passing test
Goal: Have an empty, runnable project with a working test harness.
Description: Initialize the repository structure for the chosen stack (frontend/backend/build tooling as applicable), wire up a test runner, and add one trivial test that passes. This confirms the toolchain works before any real feature is built.
Acceptance Criteria:
- `uv sync` installs dependencies without error
- `uv run pytest` runs and at least one test passes
- Repository has a source directory and a `tests/` directory

## 2. Core data model for projects, cycles, and cards
Goal: Define and persist the core entities the product is built on.
Description: Create the data model (and migrations, if applicable) for Project, Feedback Cycle, Feedback Card, Cluster, Vote, Decision, and Action Item, including how they relate to each other. `Cluster` is the same thing later tasks call a "topic" once voting/discussion starts — don't add a separate Topic entity. User-linked fields (e.g. `Card.author`, `Vote.participant`, `ActionItem.owner`) are added by the tasks that introduce users and membership (#3, #4), not here.
Depends on: #1
Acceptance Criteria:
- Project, Feedback Cycle, Feedback Card, Cluster, Vote, Decision, and Action Item exist as models with their relationships (e.g. a cycle belongs to a project, a card belongs to a cycle)
- Migrations run cleanly against a fresh database
- Card category, anonymity flag, and action status fields are present
- There is a way to store a free-text discussion note tied to a cluster/topic (its own model or a field — implementer's choice), for #16 and #22 to use

## 3. User authentication
Goal: Let a person sign up and log in to the app.
Description: Implement account creation and login (email/password or a chosen provider), with sessions or tokens protecting authenticated routes, and add the `User` entity itself (it isn't part of #2's data model). No roles or permissions logic yet — just "who is this user."
Depends on: #1
Acceptance Criteria:
- A new user can sign up with an email and password
- A registered user can log in and receive a valid session/token
- A request to a protected route without a valid session/token is rejected
- Passwords are stored hashed, never in plain text
- A reusable "current user" dependency/function exists that other routes can depend on — #4's permission check is built on top of it

## 4. Configurable roles and permissions
Goal: A route can require a specific role (e.g. "facilitator") on a specific project by depending on a reusable FastAPI dependency, with roles stored as data rather than hard-coded in application logic.
Acceptance Criteria:
- [ ] A `Role` table (or equivalent) exists with two seeded rows: `team_member` and `facilitator` — querying the table after migrations run shows both rows, without reading any Python source
- [ ] A `ProjectMembership` table exists with a `user_id` (FK to `users`), a `project_id` (FK to `projects`), and a role reference (FK to the seeded role data) — there is a unique constraint so one user has at most one row per project
- [ ] A new Alembic migration creates the `roles` and `project_memberships` tables and inserts the two seeded role rows; running `uv run alembic upgrade head` against a fresh database leaves exactly those two role rows present
- [ ] A `require_role(role_name)` FastAPI dependency exists in `app/security.py`, built on top of `get_current_user`, that resolves the caller's `ProjectMembership` for a project id (e.g. taken from a path parameter) and checks its role — there is no `if role == "facilitator"` (or similar) branching anywhere else in the codebase
- [ ] One example route is added (e.g. `GET /projects/{project_id}/facilitator-ping`) guarded by `require_role("facilitator")`, solely to prove the dependency end-to-end — it is scaffolding for this task, not a product feature
- [ ] A test calls the example route as a user with a `team_member` membership on that project and gets `403`
- [ ] A test calls the example route as a user with a `facilitator` membership on that project and gets `200`
- [ ] A test calls the example route with no auth token (or an invalid one) and gets `401`, showing `get_current_user`'s existing check still runs before the role check
- [ ] A test calls the example route as a user who is authenticated but has no `ProjectMembership` row at all on that project and gets `403`, not a `500`
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- Any UI or endpoint for creating, renaming, or assigning custom roles — the MVP only ever needs the two seeded roles (`team_member`, `facilitator`); this plan doesn't call for role management as a feature anywhere, so no follow-up issue is filed for it
- Wiring `require_role` into real feature routes — facilitator-only cycle creation (#6), reveal (#10), discussion status changes (#15), meeting uploads (#18), and draft confirmation (#21) each apply the dependency when they build their own routes; those routes don't exist yet
- The project-creation flow and "creator becomes Facilitator" behavior — that's #5's job; this task only needs a `Project` row to exist for its own tests
Constraints:
- Builds on #2 (Core data model) and #3 (User authentication), both merged: the `User` model and `get_current_user` dependency already exist and should not be re-implemented
- Add `Role` and `ProjectMembership` as new classes in `app/models.py`, following the existing SQLAlchemy 2.0 declarative style (`Mapped[...]` / `mapped_column`, `relationship(back_populates=...)`) used by `User`, `Project`, etc.
- Add the permission-check dependency to `app/security.py`, next to `get_current_user`, and have it depend on `get_current_user` rather than re-parsing the auth token
- Generate the schema change with `uv run alembic revision --autogenerate -m "..."`, matching the existing files in `migrations/versions/` (`a1e32fb74dd2_add_users_and_auth_tokens.py` is the most recent), and seed the two role rows inside that same migration's `upgrade()`
- Put the example route in a new small router (e.g. `app/example_protected.py`) included from `app/main.py` the same way `app/auth.py`'s router is, so it can be deleted later without touching real feature code
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py` (`Base.metadata.create_all` on a `StaticPool` `sqlite:///:memory:` engine, `app.dependency_overrides[get_db]`)

## 5. Create and view a project
Goal: A logged-in user can create a project (a required `name`, plus optional `description`, `start_date`, `end_date`) and view it afterward on its own page, with the creator automatically recorded as the project's Facilitator so later facilitator-only actions (like #6's cycle creation) have someone able to pass that check.
Acceptance Criteria:
- [ ] A logged-in user can create a project (e.g. `POST /projects`) by submitting `name` (required) plus optional `description`, `start_date`, and `end_date`; the response includes the new project's id and the submitted fields
- [ ] A request to create a project with no `name`, or a `name` that is empty or only whitespace, is rejected with a 422 response and no project row is created
- [ ] A request to create a project without a valid auth token is rejected with 401, matching the existing behavior of other protected routes (e.g. `GET /auth/me`)
- [ ] Creating a project also records the creator as a Facilitator member of that project -- no separate action is needed for the creator to hold that role
- [ ] A created project can be retrieved afterward by its id (e.g. `GET /projects/{id}`) and shows the same `name`, `description`, `start_date`, and `end_date` that were submitted
- [ ] Requesting a project id that does not exist returns 404
- [ ] Viewing a project does not require the requester to be a member of it -- any authenticated user can view any project by id (see Out of scope for why this isn't restricted further)
- [ ] `uv run pytest` passes, including new tests covering the cases above
Out of scope:
- Editing or deleting an existing project, and listing/browsing all projects a user belongs to -- none of docs/tasks.md's later tasks call for editing, deleting, or listing projects, so no follow-up issue is filed for it
- Validating that `end_date` is not before `start_date`, or any other cross-field date validation -- nothing in the plan depends on this, so no follow-up issue is filed for it
- Restricting project visibility to only its members (vs. any authenticated user) -- it's card- and cycle-level access that #7 and #9 actually gate on membership; the plan never requires a project itself to be hidden from a logged-in non-member, so no follow-up issue is filed for it
- Adding team members beyond the creator -- that's #6's job
- The role/membership schema and the permission-checking dependency themselves -- that's #4's job; this task only needs to call into whatever #4 lands as to record the creator's Facilitator membership
Constraints:
- `Project` already exists in `app/models.py` (from #2) with `name`, `description`, `start_date`, `end_date` -- no new migration is needed for the project table itself
- Depends on #4 being merged: recording the creator's Facilitator membership must use whatever role/membership mechanism #4 lands as -- implement against #4's actual merged code, not a re-derived or parallel mechanism
- New route(s) belong in a new `app/projects.py` router, included from `app/main.py` the same way `app/auth.py`'s router is
- Follow the existing FastAPI/Pydantic pattern in `app/auth.py`: Pydantic request/response models, `response_model=...`, `Depends(get_db)`, `Depends(get_current_user)`, `HTTPException(status_code=..., detail=...)`
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 6. Create a feedback cycle and invite the team
Goal: A facilitator can open a new feedback cycle on a project and add other existing users to the project as Team Members, so the cycle has participants before feedback submission begins.
Acceptance Criteria:
- [ ] A facilitator can open a new feedback cycle on a project (e.g. `POST /projects/{project_id}/cycles`); the response includes the new cycle's `id`, `project_id`, and `status`
- [ ] A newly created cycle's `status` is `open` by default -- no separate request is needed to put it in the open state
- [ ] A cycle's `status` can be read back after creation (e.g. via `GET /projects/{project_id}/cycles/{cycle_id}`), not just returned once in the creation response
- [ ] A request to create a cycle from a user who holds only the `team_member` role on that project is rejected with `403`, via the existing `require_role("facilitator")` dependency
- [ ] A request to create a cycle without a valid auth token is rejected with `401`
- [ ] A request to create a cycle from a user who is authenticated but has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] A facilitator can add another existing user to the project as a Team Member (e.g. `POST /projects/{project_id}/members` identifying the user by `email`); this creates a `ProjectMembership` row referencing the existing seeded `team_member` `Role` row -- no new `Role` row is created
- [ ] Adding a user who already has a `ProjectMembership` on that project (as either role) is rejected with `409`, and no duplicate `ProjectMembership` row is created
- [ ] Adding a user by an `email` with no matching `User` account is rejected with `404`
- [ ] A request to add a member from a user who holds only the `team_member` role on that project is rejected with `403`
- [ ] A request to add a member without a valid auth token is rejected with `401`
- [ ] `uv run pytest` passes, including new tests covering the cases above
Out of scope:
- Closing a cycle, revealing it, or any other status transition off of `open` -- moving a cycle to `revealed` is #10's job; nothing in this task exercises `CycleStatus.REVEALED` or `CycleStatus.CLOSED`, and no task in the current backlog is yet assigned ownership of the `open` to `closed` transition -- worth a follow-up issue when that gap is picked up, not filed here since it's outside this task's mandate
- Listing all members of a project, and removing or changing a member's role once added -- no task in docs/tasks.md's plan calls for this, so no follow-up issue is filed for it
- A user adding themselves to a project (self-service join) -- only a facilitator adding someone else is in scope here
- Feedback card submission itself -- that's #7's job, which depends on this task for a cycle and members to exist
- Creating the `team_member` role, the `ProjectMembership` table, or the permission-checking dependency -- that's #4's job, already merged; this task only looks up and reuses what #4 landed as
Constraints:
- `FeedbackCycle`, `Role`, and `ProjectMembership` already exist in `app/models.py` (from #2 and #4) -- no new migration is needed for this task
- Depends on #4 being merged: use the existing `require_role("facilitator")` dependency from `app/security.py` for both new routes rather than a re-derived permission check
- Depends on #5 being merged: a `Project` must already exist (via `POST /projects`) before a cycle can be opened on it
- New route(s) belong in a new `app/cycles.py` router (or extend `app/projects.py` -- implementer's choice, matching the existing router-per-resource pattern), included from `app/main.py` the same way `app/auth.py`'s router is
- Follow the existing FastAPI/Pydantic pattern in `app/auth.py` and `app/projects.py`: Pydantic request/response models, `response_model=...`, `Depends(get_db)`, `Depends(require_role("facilitator"))`, `HTTPException(status_code=..., detail=...)`
- Look up the `team_member` `Role` row by name at request time (the same way `app/projects.py` already looks up `facilitator` when recording a project's creator) -- don't hardcode a role id
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 7. Feedback submission form (Start / Stop / Continue)
Goal: A logged-in member of a project can submit multiple feedback cards -- each tagged Start, Stop, or Continue -- against an open cycle of that project, and each card is persisted with a link to the cycle and to the member who submitted it.
Acceptance Criteria:
- [ ] A member of the project (`team_member` or `facilitator`) can submit a feedback card to an open cycle (e.g. `POST /projects/{project_id}/cycles/{cycle_id}/cards`) with `category` (`start`, `stop`, or `continue`) and `text`; the response is `201` and includes the card's `id`, `cycle_id`, `category`, and `text`
- [ ] A member can submit more than one card under the same category in the same cycle -- e.g. two separate `start` submissions both persist as distinct rows
- [ ] A member can submit cards under all three categories (`start`, `stop`, `continue`) in the same cycle
- [ ] Each submitted card is persisted with its `cycle_id` set to the cycle it was submitted to
- [ ] Each submitted card is persisted with a new `author_id` foreign key referencing the submitting user -- a column that does not yet exist on `feedback_cards` and that this task adds via a migration; cards submitted by two different members of the same cycle are attributable to their correct, distinct authors
- [ ] A request with a `category` outside `start`/`stop`/`continue` is rejected with `422` and no card is created
- [ ] A request with blank or whitespace-only `text` is rejected with `422` and no card is created
- [ ] A request to submit a card to a cycle whose `status` is `revealed` is rejected with `409` and no card is created
- [ ] A request to submit a card to a cycle whose `status` is `closed` is rejected with `409` and no card is created
- [ ] A request to submit a card without a valid auth token is rejected with `401`
- [ ] A request to submit a card from an authenticated user who has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] A request naming a `cycle_id` that exists but belongs to a different project than the `project_id` in the path is rejected with `404`
- [ ] A request naming a `cycle_id` that does not exist at all is rejected with `404`
- [ ] `uv run pytest` passes, including new tests covering the cases above
Out of scope:
- Marking a card anonymous at submission time -- the `is_anonymous` column already exists on `FeedbackCard` (from #2) but this task's request schema does not expose it, so every card created here has `is_anonymous = false`; adding the checkbox and hiding the author everywhere (including from the facilitator) is #8's job
- Viewing or listing submitted cards, before or after reveal -- that's #9 (private pre-reveal view) and #10 (facilitator reveal)'s job, both of which depend on this task existing
- Editing or deleting a previously submitted card -- #9 is where editing one's own pre-reveal cards is introduced
- Any clustering, grouping, or assignment of a card to a `Cluster` -- that's #11's job; cards created here always have `cluster_id = null`
- A frontend UI/form -- consistent with every other task in this backlog so far (#4-#6), this task only builds the API endpoint(s); no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up issue is filed for it
Constraints:
- `FeedbackCard`, `FeedbackCycle`, and `ProjectMembership` already exist in `app/models.py` (from #2 and #4); this task adds the missing `author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))` column to `FeedbackCard` (plus the corresponding `author`/`cards` relationship pair with `User`), generated via `uv run alembic revision --autogenerate -m "..."`, matching the existing files in `migrations/versions/` (`8489561c7db3_add_roles_and_project_memberships.py` is the most recent)
- No dependency in `app/security.py` currently checks for "any project membership regardless of role" -- `require_role(role_name)` only matches one named role. Add a reusable dependency (e.g. `require_project_member`) next to `require_role`, built the same way (depends on `get_current_user`, resolves the `ProjectMembership` for the `project_id` path parameter, `403` if none exists) but without a role-name check, so members of either role can submit cards
- New route(s) belong in a new `app/cards.py` router (or extend an existing cycles router -- implementer's choice, matching the existing router-per-resource pattern), included from `app/main.py` the same way `app/auth.py`'s router is
- Follow the existing FastAPI/Pydantic pattern in `app/auth.py` and `app/projects.py`: Pydantic request/response models, `response_model=...`, `Depends(get_db)`, `HTTPException(status_code=..., detail=...)`
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 8. Anonymous submission handling
Goal: Let contributors mark individual cards as anonymous, and enforce it everywhere.
Description: Add an anonymous checkbox per card, and make sure the author is hidden from every view and API response for anonymous cards — including to the facilitator. Write a test that specifically checks a facilitator-facing view cannot see the anonymous author.
Depends on: #7 (Feedback submission form)
Acceptance Criteria:
- A card can be flagged anonymous at submission time
- No view or API response exposes the author of an anonymous card, including to the facilitator
- A test explicitly verifies a facilitator-facing endpoint/view cannot retrieve the anonymous author

## 9. Private view of own feedback before reveal
Goal: Let a member see and edit only their own cards before the reveal.
Description: Build the pre-reveal view of the feedback form/list that shows a user their own submitted cards (editable) and gives no visibility into anyone else's submissions. Enforce this on the backend, not just by hiding UI elements.
Depends on: #7 (Feedback submission form)
Acceptance Criteria:
- Before reveal, a member's view lists only their own cards and allows editing them
- A backend request for another member's pre-reveal cards is rejected, not just hidden in the UI
- The check holds even when the requesting user is the facilitator

## 10. Facilitator reveal action
Goal: Let the facilitator make all submitted cards visible to the team at once.
Description: Add a "reveal" action, available only to the facilitator, that flips the cycle into a revealed state and makes every submitted card (respecting anonymity) visible to all team members in a single view.
Depends on: #4 (Configurable roles and permissions), #9 (Private view of own feedback before reveal)
Acceptance Criteria:
- Only the facilitator can trigger reveal on an open cycle
- After reveal, the cycle's state reflects "revealed" and all cards become visible to every team member
- Anonymous cards remain anonymous after reveal
- A non-facilitator attempting to reveal is denied

## 11. Manual clustering board
Goal: Let the team organize revealed cards into clusters by hand.
Description: Build a board view of revealed cards where users can move cards between clusters, merge or split clusters, rename clusters, and leave cards ungrouped. Persist cluster membership so it survives a page reload.
Depends on: #2 (Core data model), #10 (Facilitator reveal action)
Acceptance Criteria:
- Cards can be moved between clusters, and clusters can be merged, split, and renamed
- Cards can be left ungrouped
- Cluster membership is persisted and still correct after a reload

## 12. Automatic clustering suggestions
Goal: Pre-group revealed cards into suggested clusters using AI.
Description: When a cycle is revealed, call an AI service to propose an initial clustering of the cards and populate the clustering board with these suggestions. The output must be fully editable using the manual clustering controls — this task only adds the suggestion step, not any locking behavior. Requires an AI/LLM provider to be configured; treat the specific provider and credentials as a config/environment concern, not something this task needs to design.
Depends on: #11 (Manual clustering board)
Acceptance Criteria:
- On reveal, an AI-suggested clustering is generated and populates the board
- Suggested clusters can be edited with the same controls built in task 11
- If the AI call fails, the board still loads (cards ungrouped) instead of erroring

## 13. Voting on discussion topics
Goal: Let each team member distribute 3 votes across clusters.
Description: Add a voting UI where each participant gets exactly 3 votes to allocate across clusters, including the ability to put more than one vote on the same cluster. Store each vote so results can be tallied later.
Depends on: #2 (Core data model), #3 (User authentication), #11 (Manual clustering board)
Acceptance Criteria:
- Each participant has exactly 3 votes to allocate per cycle
- A participant can place more than one vote on the same cluster
- A participant cannot cast more than 3 total votes
- Votes are persisted per participant per cluster

## 14. Reveal vote results after voting closes
Goal: Show vote totals only once voting is complete.
Description: Hide individual and running vote totals while voting is open, and reveal the final tally (ranked by votes) once every participant has voted or the facilitator manually closes voting. This produces the prioritized discussion agenda referenced elsewhere in the plan.
Depends on: #4 (Configurable roles and permissions), #13 (Voting on discussion topics)
Acceptance Criteria:
- Vote totals are not visible to participants while voting is open
- Totals become visible once all participants have voted, or the facilitator closes voting
- The results view ranks clusters by total votes

## 15. Discussion stage with topic status
Goal: Let the facilitator move through the vote-ranked topics during the meeting, and close the cycle when the meeting ends.
Description: Build a discussion view listing topics in vote order, where the facilitator can mark each one Discussed, Skipped, or Deferred. This is a live, in-meeting control surface, not a historical report. "Topic" here is the same `Cluster` entity from #2/#11 — reveal it in vote order rather than modeling anything new. Nothing in the backlog before this task ever moves a cycle out of the `revealed` state, but #18 (meeting upload) requires a `closed` cycle — so this task also owns the facilitator action that closes the cycle once the discussion is done (a real gap found while grooming #6, flagged here since this is the natural place to close it: the last live-meeting control surface before upload/summary tasks take over).
Depends on: #4 (Configurable roles and permissions), #14 (Reveal vote results after voting closes)
Acceptance Criteria:
- Topics are listed in vote-ranked order
- The facilitator can set a topic's status to Discussed, Skipped, or Deferred
- A non-facilitator cannot change topic status
- Status changes are reflected immediately to other viewers
- The facilitator can close the cycle (status -> `closed`), and a non-facilitator cannot

## 16. Record notes, decisions, and action items during discussion
Goal: Let the team capture outcomes while discussing a topic.
Description: On the discussion view, let any team member add free-text notes and structured decision/action-item entries tied to the topic currently being discussed. This is manual entry during the live meeting, separate from the later AI-assisted extraction from recordings. Uses the `Decision`, `ActionItem`, and note storage added in #2.
Depends on: #2 (Core data model), #15 (Discussion stage with topic status)
Acceptance Criteria:
- Any team member can add a free-text note tied to the topic currently being discussed
- A decision or action item can be added and is linked to that topic
- Newly added entries are visible to other participants without a page reload

## 17. Action item tracking and status updates
Goal: Give action items a life beyond the meeting they were created in.
Description: Build a view listing all action items for a project with their description, owner, optional due date, status (Open/Done), and related topic. Let the assigned owner mark their own action items as Done.
Depends on: #3 (User authentication), #16 (Record notes, decisions, and action items during discussion)
Acceptance Criteria:
- A project-level view lists all action items with description, owner, due date, status, and related topic
- The assigned owner can mark their own action item as Done
- A user who is not the owner cannot change its status

## 18. Meeting upload page
Goal: Let the facilitator attach a record of the meeting after it happens.
Description: Build an upload page where the facilitator can attach audio, video, a transcript file, or pasted transcript text to a closed cycle (the open/closed state from #6 — there's no separate "completed" state), and see the upload's processing status. This task covers the upload and status UI only, not the processing itself.
Depends on: #4 (Configurable roles and permissions), #6 (Create a feedback cycle and invite the team)
Acceptance Criteria:
- The facilitator can upload audio, video, a transcript file, or paste transcript text against a closed cycle
- The page displays a processing status field
- A non-facilitator cannot upload to a cycle

## 19. Transcript generation from uploaded audio/video
Goal: Turn an uploaded audio or video file into text.
Description: Add a background job that takes an uploaded audio/video file, runs it through a transcription service, and stores the resulting transcript text against the cycle. Update the processing status so the upload page (task 18) can reflect progress and completion. Requires a transcription provider to be configured; treat the specific provider/credentials as a config concern, not something this task needs to design.
Depends on: #18 (Meeting upload page)
Acceptance Criteria:
- Uploading audio/video triggers a background transcription job
- The resulting transcript text is stored against the cycle
- The cycle's processing status updates to reflect progress and completion
- A failed transcription sets the status to a failed/error state rather than leaving it stuck in progress

## 20. AI extraction of decisions and action items
Goal: Turn a transcript into draft decisions and action items.
Description: Add a background job that sends a stored transcript to an AI service and produces a draft list of decisions, action items (with owner and due date when mentioned), and a short summary. Save these as unconfirmed drafts — nothing here is shown to the team as final yet. Uses the `Decision` and `ActionItem` entities from #2, marked unconfirmed; this is a second path into those tables alongside the manual entry from #16.
Depends on: #2 (Core data model), #19 (Transcript generation from uploaded audio/video)
Acceptance Criteria:
- A stored transcript can be processed into draft decisions, action items, and a short summary
- Extracted action items capture owner and due date when they are mentioned in the transcript
- Draft output is saved as unconfirmed and does not appear anywhere team-facing yet

## 21. Facilitator review and confirmation of extracted items
Goal: Let the facilitator approve, edit, or discard AI-suggested outcomes.
Description: Build a review screen showing the AI-drafted decisions, action items, and summary from task 20, where the facilitator can edit any field and confirm each item before it becomes part of the permanent record. Nothing from the extraction job should reach the team-facing summary without going through this step.
Depends on: #4 (Configurable roles and permissions), #20 (AI extraction of decisions and action items)
Acceptance Criteria:
- The facilitator can view, edit, and confirm or discard each AI-drafted decision, action item, and the summary
- Confirmed items are saved to the permanent record; discarded items are not
- A non-facilitator cannot confirm or discard drafts

## 22. Retrospective summary page
Goal: Give the team a single page summarizing a completed cycle.
Description: Build a summary page showing the top discussion topics, key notes, confirmed decisions, confirmed action items, attendance/participation, and the original feedback cards for a closed cycle. This is a read-only page assembled from data produced by earlier tasks. Nothing tracks "attendance" as its own concept — derive participation from who submitted a card or cast a vote in the cycle; no new entity is needed.
Depends on: #16 (Record notes, decisions, and action items during discussion), #17 (Action item tracking and status updates), #21 (Facilitator review and confirmation of extracted items)
Acceptance Criteria:
- The summary page shows top discussion topics, key notes, confirmed decisions, confirmed action items, attendance/participation, and original feedback cards for a closed cycle
- The page is read-only
- Unconfirmed AI drafts never appear on this page

## 23. Project dashboard page
Goal: Give a project a home page summarizing its ongoing state.
Description: Build the main project page showing the current feedback cycle and submission status, any active retrospective, links to previous retrospectives, and a list of open action items. This ties together the project-level views built in other tasks into one landing page.
Depends on: #6 (Create a feedback cycle and invite the team), #17 (Action item tracking and status updates), #22 (Retrospective summary page)
Acceptance Criteria:
- The project page shows the current feedback cycle and submission status
- The project page links to an active retrospective, when one exists, and to previous retrospectives
- The project page lists the project's open action items
