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
Goal: A contributor can mark a feedback card anonymous when submitting it, and every response that serializes a `FeedbackCard` -- today's submission endpoint and every endpoint added later -- omits the author's identity for a card flagged anonymous, with no exception for the facilitator.
Acceptance Criteria:
- [ ] The card submission endpoint (`POST /projects/{project_id}/cycles/{cycle_id}/cards`, from #7) accepts an optional `is_anonymous` boolean field in its request body; omitting it defaults to `false`, matching the column's existing default
- [ ] A card submitted with `is_anonymous: true` is persisted with `is_anonymous = True`, and its `author_id` is still set to the submitting user -- anonymity hides the author from output, it does not stop the system from recording who actually submitted it
- [ ] A card submitted with `is_anonymous` omitted or explicitly `false` is persisted with `is_anonymous = False`
- [ ] A reusable card-serialization function (e.g. `serialize_feedback_card`) or response schema is added that every endpoint returning `FeedbackCard` data must use; called on a card with `is_anonymous = True`, its output contains no author-identifying field (no `author_id`, no nested author email or name) for any caller, including a facilitator
- [ ] The same serializer, called on a card with `is_anonymous = False`, includes the author's identity (e.g. `author_id`)
- [ ] A test exercises the serializer directly against both an anonymous and a non-anonymous card and asserts the author field is present or absent accordingly -- the closest thing to "a facilitator-facing view cannot see the anonymous author" verifiable today, since no card-listing/retrieval endpoint exists yet (see Out of scope)
- [ ] A test confirms the submission endpoint's own response body -- the only live card-returning response as of this task -- never includes an author field, for both anonymous and non-anonymous cards, consistent with #7's existing response shape (`id`, `cycle_id`, `category`, `text`)
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- Any endpoint that lists or retrieves previously submitted cards (a member's own pre-reveal cards, the facilitator's post-reveal view, etc.) -- that's #9 and #10's job; neither exists yet, so this task cannot test an actual "facilitator-facing view." #9 and #10 must use the serializer this task adds and must each add their own test proving an anonymous card's author is hidden from their specific view -- for #10 specifically, from the facilitator
- Editing an already-submitted card's `is_anonymous` flag after creation -- #9 introduces editing a member's own pre-reveal cards; whether that extends to toggling anonymity is #9's call, not this task's
- A frontend UI/checkbox -- consistent with #4-#7, this task only builds the API-level field and serialization; no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up issue is filed for it
Constraints:
- `FeedbackCard.is_anonymous` already exists on the model (added in #2) -- no migration is needed for the column itself
- Depends on #7 being merged: add `is_anonymous` to whatever Pydantic request model #7 lands for card submission, in `app/cards.py`
- Add the serialization helper somewhere shared (e.g. in `app/cards.py`, or a new small module) so #9 and #10 can import and reuse it rather than re-deriving their own card-to-response logic
- The rule is unconditional: no role, including facilitator, is an exception to hiding the author of an anonymous card
- Follow the existing FastAPI/Pydantic pattern in `app/auth.py`, `app/projects.py`, and #7's `app/cards.py`
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 9. Private view of own feedback before reveal
Goal: A member of a project can list only their own submitted feedback cards for a cycle, and can edit one of their own cards' category, text, and anonymity flag while that cycle is still open, using #8's shared card-serializer for every response; no other member's cards -- including from the facilitator -- are ever visible or editable through these endpoints.
Acceptance Criteria:
- [ ] A member can list their own cards in a cycle (e.g. `GET /projects/{project_id}/cycles/{cycle_id}/cards/mine`); the response is a list of cards produced by #8's shared serializer (e.g. `serialize_feedback_card`), containing only cards where `author_id` equals the requesting user's id
- [ ] The filtering to "only my cards" happens at the query level (`WHERE author_id = current_user.id`), not by fetching every card in the cycle and hiding fields after the fact -- another member's card is never present in the response body at all, in any cycle status (`open`, `revealed`, `closed`)
- [ ] A test proves that a user's own anonymous card (`is_anonymous: true`) appears in their own "list mine" response with its `text` and `category` intact -- #8's serializer only omits author-identifying fields, it does not hide the card itself from its own author
- [ ] A test proves that when user A and user B both belong to the same project and cycle and have each submitted cards, user B's "list mine" response contains none of user A's cards, and user A's contains none of user B's -- not "anonymized", but entirely absent
- [ ] A test proves the same isolation holds when the requester is the facilitator: a facilitator who has submitted no cards of their own gets an empty list from "list mine" before reveal, never a team member's cards
- [ ] Listing own cards for a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, returns `404` (matching #7's existing pattern for cycle lookups)
- [ ] A member can edit one of their own cards while its cycle's `status` is `open` (e.g. `PUT /projects/{project_id}/cycles/{cycle_id}/cards/{card_id}`), submitting `category`, `text`, and `is_anonymous` -- all three required on every request, no partial update -- and the `200` response reflects the updated values via #8's serializer
- [ ] Editing a card owned by a different user returns `404`, not `403` -- consistent with this task's "no visibility into anyone else's submissions" goal, the response must not confirm that a card with that id exists and belongs to someone else; this holds even when the requester is the facilitator
- [ ] Editing a card whose cycle `status` is `revealed` or `closed` is rejected with `409`, and the card's fields are left unchanged
- [ ] Editing a card with a `category` outside `start`/`stop`/`continue`, or with blank/whitespace-only `text`, is rejected with `422`, and the card's fields are left unchanged
- [ ] Editing a `card_id` that does not exist at all returns `404`
- [ ] Editing a `card_id` that exists but belongs to a different `cycle_id`/`project_id` than the path returns `404`
- [ ] Both endpoints reject a request without a valid auth token with `401`
- [ ] Both endpoints reject a request from an authenticated user with no `ProjectMembership` on that project at all with `403`, not `500`
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- Viewing or editing a card that isn't the requester's own, including a facilitator's access to team members' pre-reveal cards -- there is no view for that in this task; a facilitator (or team member) seeing every card together only exists after reveal, which is #10's job
- Listing/viewing all cards in a cycle after reveal (any card, any author) -- that's #10's job, which depends on this task
- Deleting a submitted card -- nothing in docs/tasks.md's plan calls for card deletion anywhere, so no follow-up issue is filed for it
- Partial-field updates on edit (e.g. changing only `text` without resubmitting `category` and `is_anonymous`) -- this task's edit endpoint requires all three fields on every request, specifically to avoid silently resetting `is_anonymous` to a default via an omitted field on an already-anonymous card; nothing else in the plan needs a partial-update endpoint, so no follow-up issue is filed for it
- Any UI/frontend for viewing or editing cards -- consistent with #4-#8, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up issue is filed for it
- Cluster assignment or any clustering-related display in this view -- that's #11's job; cards returned here keep whatever `cluster_id` #7 gave them (`null`), unchanged by this task
Constraints:
- Depends on #7 (Feedback submission form) being merged: `FeedbackCard`, the `require_project_member` dependency, and the existing `app/cards.py` request/response patterns already exist and must be reused, not re-derived
- Depends on #8 (Anonymous submission handling) being merged: both new endpoints must call #8's shared card-serialization helper (e.g. `serialize_feedback_card`) for every response body -- no separate/ad-hoc logic that re-derives which fields to hide for an anonymous card. This task cannot start until #8 lands, since the serializer it must use does not exist yet
- Add both new routes to the existing `app/cards.py` router, following the existing FastAPI/Pydantic pattern in `app/auth.py`, `app/projects.py`, and #7/#8's `app/cards.py`: Pydantic request/response models, `response_model=...`, `Depends(get_db)`, `Depends(require_project_member)`, `HTTPException(status_code=..., detail=...)`
- Use `require_project_member` (not `require_role`) for both routes -- both `team_member` and `facilitator` must be able to view/edit their own cards, and neither role gets special access to another member's cards
- Use the `/cards/mine` sub-path (not the bare `/cards` collection) for the list endpoint, so it doesn't collide with the all-cards listing #10 will likely add at `GET /projects/{project_id}/cycles/{cycle_id}/cards`
- No new migration is needed -- `FeedbackCard.category`, `.text`, `.is_anonymous`, and `.author_id` all already exist (from #2, #7, #8)
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 10. Facilitator reveal action
Goal: The facilitator can reveal an open cycle, flipping its status to `revealed`, after which every project member (team member or facilitator) can view every card submitted to that cycle in a single list -- reusing #8's shared card-serializer so an anonymous card's author stays hidden from everyone, including the facilitator who triggered the reveal.
Acceptance Criteria:
- [ ] A facilitator can reveal an open cycle (e.g. `POST /projects/{project_id}/cycles/{cycle_id}/reveal`); the response is `200` and includes the cycle's `id`, `project_id`, and updated `status`
- [ ] Revealing a cycle whose `status` is `open` sets its `status` to `revealed`; fetching the cycle afterward (e.g. `GET /projects/{project_id}/cycles/{cycle_id}`, from #6) reflects `revealed`, not just the reveal response itself
- [ ] Revealing a cycle whose `status` is already `revealed` is rejected with `409`, and the cycle's `status` is left unchanged
- [ ] Revealing a cycle whose `status` is `closed` is rejected with `409`, and the cycle's `status` is left unchanged
- [ ] A request to reveal from a user who holds only the `team_member` role on that project is rejected with `403`, via the existing `require_role("facilitator")` dependency
- [ ] A request to reveal without a valid auth token is rejected with `401`
- [ ] A request to reveal from a user who is authenticated but has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] Revealing a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`
- [ ] Any project member (`team_member` or `facilitator`) can list every card submitted to a `revealed` or `closed` cycle in one call (e.g. `GET /projects/{project_id}/cycles/{cycle_id}/cards`); the response is a list of cards produced by #8's shared serializer (e.g. `serialize_feedback_card`), covering every card in the cycle regardless of author -- not filtered to the requester's own, unlike #9's `/mine`
- [ ] A test proves that after reveal, the all-cards response includes cards submitted by multiple different authors in the same cycle, contrasting with #9's per-author isolation
- [ ] A test proves that a facilitator viewing the all-cards response after reveal cannot see the author of a card submitted with `is_anonymous: true` -- no `author_id` or other author-identifying field appears for that card, using #8's serializer's existing contract, while a non-anonymous card in the same response does show its author's identity
- [ ] Listing all cards for a cycle whose `status` is still `open` is rejected with `409` -- the all-cards view only exists once reveal has happened; a member's own pre-reveal cards remain visible only through #9's `/mine`
- [ ] Listing all cards for a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`
- [ ] A request to list all cards without a valid auth token is rejected with `401`
- [ ] A request to list all cards from a user who is authenticated but has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- Reverting a `revealed` cycle back to `open` -- nothing in docs/tasks.md's plan calls for un-revealing a cycle, so no follow-up issue is filed for it
- Closing a cycle (`revealed` -> `closed`) -- that's #15's job, which already owns the cycle-close action as the last step of the live-meeting discussion stage
- A clustering/board view of revealed cards -- that's #11's job, which depends on this task for cards to be visible in the first place
- Sorting, filtering, or grouping the all-cards response by category or cluster -- nothing in the plan calls for it at this stage; #11 introduces cluster assignment and its own board view
- Editing or deleting a card after reveal -- #9 already rejects edits to a card once its cycle is `revealed` or `closed`; this task does not change that
- A frontend UI for the reveal action or the all-cards view -- consistent with #4-#9, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up issue is filed for it
Constraints:
- Depends on #4 (Configurable roles and permissions) being merged: use the existing `require_role("facilitator")` dependency from `app/security.py` for the reveal route, and `require_project_member` for the all-cards route -- both team_member and facilitator can view, neither gets special treatment
- Depends on #9 (Private view of own feedback before reveal) being merged, which itself depends on #8 (Anonymous submission handling): the all-cards endpoint must call #8's shared card-serialization helper (e.g. `serialize_feedback_card`) for every card in its response -- no separate/ad-hoc logic that re-derives which fields to hide for an anonymous card. This task cannot start until #9 lands
- `FeedbackCycle.status` and the `CycleStatus` enum (`open`, `revealed`, `closed`) already exist in `app/models.py` (from #2) -- no new migration is needed for this task
- Add the reveal route to the existing `app/cycles.py` router, alongside `create_cycle` and `get_cycle`, following the same Pydantic request/response, `Depends(get_db)`, `HTTPException(status_code=..., detail=...)` pattern
- Add the all-cards route to the existing `app/cards.py` router, alongside #7/#8/#9's card routes, so it can reuse their serializer and query patterns directly
- Use the `GET /projects/{project_id}/cycles/{cycle_id}/cards` path (the bare collection, not `/mine`) for the all-cards endpoint, as anticipated by #9's constraints
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 11. Manual clustering board
Goal: Any project member (`team_member` or `facilitator`) can organize a revealed cycle's cards into clusters -- creating clusters, moving cards between them (including back to ungrouped), renaming clusters, and merging clusters -- with every change persisted immediately, reusing #8's shared card-serializer (`serialize_feedback_card`, which already returns `cluster_id`) so the board's state is always reconstructable from a fresh `GET`.
Acceptance Criteria:
- [ ] A project member can create a new, empty cluster on a revealed cycle (`POST /projects/{project_id}/cycles/{cycle_id}/clusters`) with an optional `name`; the response is `201` and includes the cluster's `id`, `cycle_id`, and `name` (`null` if omitted)
- [ ] Creating a cluster on a cycle whose `status` is `open` or `closed` is rejected with `409`, and no cluster row is created
- [ ] A project member can list every cluster in a cycle (`GET /projects/{project_id}/cycles/{cycle_id}/clusters`); each entry includes `id`, `cycle_id`, and `name`
- [ ] Listing clusters for a cycle whose `status` is `open` is rejected with `409` -- no cluster can exist before reveal, matching #10's gating of the all-cards endpoint
- [ ] Listing clusters for a `revealed` or `closed` cycle returns `200` -- the list stays readable after the cycle is later closed, consistent with #10's all-cards endpoint remaining readable post-close
- [ ] A project member can rename an existing cluster (`PATCH /projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}` with body `{"name": "..."}`); the `200` response reflects the new `name`
- [ ] Renaming a cluster with a blank or whitespace-only `name` is rejected with `422`, and the cluster's `name` is left unchanged
- [ ] Renaming a `cluster_id` that does not exist, or that belongs to a different `cycle_id`/`project_id` than the path, is rejected with `404`
- [ ] Renaming a cluster on a cycle whose `status` is not `revealed` (i.e. `open` or `closed`) is rejected with `409`
- [ ] A project member can reassign a card to a different cluster (`PATCH /projects/{project_id}/cycles/{cycle_id}/cards/{card_id}/cluster` with body `{"cluster_id": <id>}`); the `200` response is the card produced by #8's `serialize_feedback_card`, showing the updated `cluster_id`
- [ ] A project member can leave a card ungrouped by sending `{"cluster_id": null}` to the same endpoint; the card's `cluster_id` becomes `null` and the card continues to appear in #10's all-cards listing (`GET /projects/{project_id}/cycles/{cycle_id}/cards`) with `cluster_id: null`
- [ ] Reassigning a card to a `cluster_id` that does not exist, or that belongs to a different `cycle_id` than the card's own cycle, is rejected with `404`, and the card's `cluster_id` is left unchanged
- [ ] Reassigning a `card_id` that does not exist, or that belongs to a different `cycle_id`/`project_id` than the path, is rejected with `404`
- [ ] Reassigning a card's cluster on a cycle whose `status` is not `revealed` is rejected with `409`
- [ ] A project member can merge one cluster into another (`POST /projects/{project_id}/cycles/{cycle_id}/clusters/{source_id}/merge-into/{target_id}`); the `200` response is the target cluster, every card previously in the source cluster now has `cluster_id` equal to the target cluster's `id`, and the source cluster no longer appears in the cluster listing afterward
- [ ] Merging a cluster into itself (`source_id == target_id`) is rejected with `422`, and no cards are moved
- [ ] Merging when either the source or target `cluster_id` does not exist, or belongs to a different `cycle_id`/`project_id` than the path, is rejected with `404`
- [ ] Merging on a cycle whose `status` is not `revealed` is rejected with `409`
- [ ] There is no dedicated "split" endpoint -- a test demonstrates that splitting a cluster is achieved by combining the create-cluster and reassign-card-cluster endpoints above: creating a new cluster and moving a subset of the original cluster's cards into it, leaving the rest in the original cluster
- [ ] A test proves persistence across a simulated reload: after creating clusters, reassigning cards (including to `null`), and merging, a fresh `GET` of the clusters list and a fresh `GET` of the all-cards list both reflect the exact same state as immediately after the mutations, with no reliance on in-memory/request-scoped state
- [ ] Each of the four mutating endpoints (create cluster, rename cluster, reassign card cluster, merge clusters) rejects a request without a valid auth token with `401`
- [ ] Each of the four mutating endpoints and the two listing endpoints (list clusters, and the reassign endpoint's cluster lookups) rejects a request from an authenticated user with no `ProjectMembership` on that project at all with `403`, not `500`
- [ ] Every endpoint in this task rejects a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, with `404`
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- Automatic/AI-suggested clustering on reveal -- that's #12's job, which depends on this task's manual controls already existing and must remain fully editable through them
- Voting on clusters -- that's #13's job, which depends on this task for clusters to vote on
- A dedicated "delete cluster" endpoint, independent of merge -- the task only names "move", "merge", "split", "rename", and "leave ungrouped" as operations; merging into another cluster is the only supported way to empty a cluster, and an empty, unmerged cluster is allowed to remain in the listing indefinitely. No follow-up filed since nothing in the plan calls for a standalone delete
- A frontend UI for the board -- consistent with #4-#10, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
- Reassigning or cascading votes, notes, decisions, or action items attached to a cluster that gets merged away -- moot for this task since #13 (voting) and #16 (notes/decisions/action items) haven't landed yet, so no `Cluster` can have any such rows attached when this task ships. Flagged here because `Cluster.votes` and `Cluster.notes` are declared with `cascade="all, delete-orphan"` in `app/models.py`, so a future merge on a cluster that does have votes/notes would delete them along with the source cluster row -- already covered by #13 and #16, which land those rows and so are the natural place to decide merge's behavior for them, so no new follow-up filed
Constraints:
- `Cluster` and `FeedbackCard.cluster_id` already exist in `app/models.py` (from #2) -- no new migration is needed for this task
- Depends on #10 (Facilitator reveal action) being merged: clustering only operates on a `revealed` cycle, and the board is reconstructed from #10's `GET /projects/{project_id}/cycles/{cycle_id}/cards` (all-cards, via #8's `serialize_feedback_card`, which already includes `cluster_id`) plus this task's new cluster-listing endpoint -- no separate "board" endpoint that duplicates card data
- Per docs/plan.md's "Reveal and cluster feedback" step ("The team can then: move cards between clusters...") and its Roles section (Team member: "Participate in clustering"), clustering is not facilitator-only -- use `require_project_member` (not `require_role("facilitator")`) for every new route, so both `team_member` and `facilitator` can act
- Every mutating endpoint (create cluster, rename cluster, reassign card cluster, merge clusters) must check `cycle.status == CycleStatus.REVEALED` and reject with `409` otherwise (both `open` and `closed` are rejected) -- mirrors the `409` gating pattern already used by #7 (card submission) and #10 (reveal, all-cards)
- Split is deliberately not a dedicated endpoint: the create-cluster and reassign-card-cluster endpoints already compose into a split (create a new cluster, move some of the original cluster's cards into it), so a third endpoint would just be sugar over the other two -- implement it as documented above, not as a new route
- Add cluster CRUD and merge routes to a new `app/clusters.py` router (`prefix="/projects"`, following the `app/cycles.py` pattern), included from `app/main.py` the same way the other routers are
- Add the card-cluster reassignment route (`PATCH .../cards/{card_id}/cluster`) to the existing `app/cards.py` router, since it operates on `FeedbackCard`, not `Cluster`, and can reuse `serialize_feedback_card` directly
- Every response that includes card data must be built through #8's `serialize_feedback_card` -- no separate/ad-hoc logic that re-derives which fields to hide for an anonymous card
- Follow the existing FastAPI/Pydantic pattern in `app/cycles.py` and `app/cards.py`: Pydantic request/response models, `response_model=...`, `Depends(get_db)`, `Depends(require_project_member)`, `HTTPException(status_code=..., detail=...)`
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 12. Automatic clustering suggestions
Goal: A project member can trigger an AI-generated clustering suggestion for a revealed cycle's still-ungrouped cards via a separate, optional endpoint -- never as part of #10's reveal action -- and the result is a set of ordinary #11 clusters, indistinguishable from and fully editable through #11's existing manual controls; a failed or unavailable AI call never produces an error and never blocks the board from loading.
Acceptance Criteria:
- [ ] A project member can trigger clustering suggestions on a revealed cycle (`POST /projects/{project_id}/cycles/{cycle_id}/suggest-clusters`); when the injectable AI-calling function returns a set of card groupings, the endpoint creates one new `Cluster` row per group (via the same creation path as #11's create-cluster endpoint), sets each returned card's `cluster_id` to its new cluster's `id`, and responds `200` with `{"status": "applied", "clusters": [...]}` where each entry has `id`, `cycle_id`, and `name`
- [ ] A fresh `GET /projects/{project_id}/cycles/{cycle_id}/cards` (#10's all-cards endpoint) after a successful suggestion reflects the assigned `cluster_id` for each grouped card, and a fresh `GET /projects/{project_id}/cycles/{cycle_id}/clusters` (#11) lists the newly created clusters -- suggestions persist through #11's existing read endpoints; no new read/board endpoint is added by this task
- [ ] When the injectable AI-calling function raises (simulating a failed or timed-out AI provider call), the endpoint catches the exception, creates no `Cluster` rows, leaves every card's `cluster_id` unchanged, and still responds `200` (never 4xx/5xx) with `{"status": "unavailable", "clusters": []}`
- [ ] A test stubs/monkeypatches the AI-calling function to return a fixed grouping and asserts the "applied" behavior above, with no real network call and no API key/credential present
- [ ] A test stubs/monkeypatches the AI-calling function to raise and asserts the "unavailable" behavior above, again with no real network call
- [ ] Only cards with `cluster_id is null` at call time are passed to, or eligible to be grouped by, the suggestion function; a card that already has a `cluster_id` (from a prior manual assignment or a prior suggestion run) is left untouched by a subsequent call
- [ ] A test proves a suggested cluster is an ordinary cluster: it can be renamed via #11's `PATCH .../clusters/{cluster_id}`, and a card can be reassigned out of it (including back to `null`) via #11's `PATCH .../cards/{card_id}/cluster`, with no special-casing or rejection
- [ ] A test proves #10's reveal action (`POST .../reveal`) succeeds with no AI-related environment variable or provider configured at all -- reveal never calls the AI-calling function and its behavior is unchanged from #10
- [ ] Calling suggest-clusters on a cycle whose `status` is `open` or `closed` is rejected with `409`, and no clusters are created and no card's `cluster_id` changes
- [ ] Calling suggest-clusters on a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`
- [ ] Calling suggest-clusters without a valid auth token is rejected with `401`
- [ ] Calling suggest-clusters from an authenticated user with no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- Which AI/LLM provider to call, prompt design, and API key/credential management -- treated as a config/environment concern; no follow-up filed since nothing later in docs/tasks.md's plan calls for choosing a provider, and the injectable function in Constraints is the extension point a future provider gets wired in behind
- Automatically re-running suggestions when new cards appear in a cycle -- moot, since #10 already blocks card submission after reveal, so a revealed cycle's card set is fixed before suggest-clusters is ever callable
- Any indication in the API of which clusters were AI-suggested vs manually created (no `origin`/`source` field) -- the goal is that a suggestion is indistinguishable from, and as fully editable as, an ordinary #11 cluster; no follow-up filed since nothing in docs/plan.md calls for surfacing this distinction
- A background job / async task queue for the AI call -- this project has no job infrastructure (no Celery/queue), and nothing in the backlog before #19 needs one; the call stays synchronous within this task's own endpoint request. No follow-up filed since nothing calls for one yet
- Automatically invoking suggest-clusters as part of #10's reveal action -- see Constraints for why this must stay a separate, optional endpoint
- A frontend UI for triggering or viewing suggestions -- consistent with #4-#11, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
Constraints:
- Depends on #11 (Manual clustering board) being merged: this task reuses #11's cluster-creation persistence and #11's `GET .../clusters` plus #10's `GET .../cards` read endpoints to expose the board state -- no new read/board endpoint is added
- Implement this as a separate, optional endpoint (`POST /projects/{project_id}/cycles/{cycle_id}/suggest-clusters`) rather than folding the AI call into #10's reveal action. Reveal must stay fast and must succeed or fail purely on its own existing rules regardless of an AI provider's availability or latency -- "if the AI call fails, the board still loads instead of erroring" is only meaningful if an AI failure can never propagate to the action that makes the board exist in the first place. This project also has no background-job infrastructure (no Celery/queue) that would make an async-during-reveal alternative safe, so a synchronous-but-decoupled endpoint is the option that avoids both blocking reveal and requiring new infra
- Use `require_project_member` (not `require_role("facilitator")`) for the new route, consistent with #11's Constraints that clustering is open to any project member, not facilitator-only
- The endpoint must check `cycle.status == CycleStatus.REVEALED` and reject with `409` otherwise (both `open` and `closed` rejected), mirroring #10/#11's gating pattern
- The AI call must go through a single, mockable function (e.g. `app/ai_clustering.py`'s `generate_cluster_suggestions(cards) -> list[SuggestedGroup]`) that the endpoint calls and catches all exceptions from -- tests must monkeypatch/stub this function so no test makes a real network call or needs a real API key/credential in CI
- No AI/LLM SDK dependency is added to `pyproject.toml` in this task -- the interface is provider-agnostic; wiring a real provider behind it is a future concern per Out of scope
- Only cards with `cluster_id is null` at call time are passed to / eligible to be grouped by the suggestion function; already-clustered cards are left untouched
- New clusters created by this endpoint use the same `Cluster` row shape as #11's create-cluster endpoint (`cycle_id`, optional `name`) so they are ordinary, fully-editable clusters with no special marker field
- Add the new route to `app/clusters.py` (from #11), following the same Pydantic request/response, `Depends(get_db)`, `Depends(require_project_member)`, `HTTPException(status_code=..., detail=...)` pattern as #11's routes
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`, stubbing/monkeypatching the AI function as described above

## 13. Voting on discussion topics
Goal: Each project member gets exactly 3 votes per revealed cycle to allocate freely across that cycle's #11 clusters, including stacking more than one vote on the same cluster; a participant can resubmit their allocation at any time while the cycle stays revealed, and each submission atomically replaces their previous one for that cycle.
Acceptance Criteria:
- [ ] A project member can cast votes on a revealed cycle in a single request (`POST /projects/{project_id}/cycles/{cycle_id}/votes`) with body `{"cluster_ids": [<id>, <id>, ...]}`, where `cluster_ids` is a list of 0 to 3 cluster ids belonging to that cycle and repeats are allowed (stacking multiple votes on the same cluster); the response is `200` with `{"cycle_id": ..., "cluster_ids": [...]}` reflecting exactly the submitted list, as a multiset -- order is not guaranteed
- [ ] Submitting `cluster_ids` with more than 3 entries (e.g. 4, including repeats) is rejected with `422`, and no `Vote` rows are created or changed
- [ ] Submitting an empty `cluster_ids` list (`[]`) succeeds with `200` and clears the participant's votes for that cycle (see the replace behavior below) -- a participant is not required to use all 3 votes, or to have voted before, to submit an empty list
- [ ] A `cluster_ids` entry that does not exist, or that belongs to a different `cycle_id` than the one in the path, causes the whole request to be rejected with `404`, and no `Vote` rows are created or changed for that participant in that cycle -- the whole submission is validated before any row is written
- [ ] Casting votes on a cycle whose `status` is `open` or `closed` is rejected with `409`, and no `Vote` rows are created or changed
- [ ] A second `POST` to the votes endpoint from the same participant on the same cycle atomically REPLACES their prior submission: their previous `Vote` rows for that cycle are deleted and only the newly submitted `cluster_ids` remain. A test proves this by casting `[A, A, B]`, then casting `[C]`, and confirming the participant's only recorded votes afterward are a single vote on `C` -- not four rows
- [ ] A test proves two votes for the same cluster in one submission (e.g. `[A, A, B]`) persist as 2 separate `Vote` rows referencing cluster `A` and 1 referencing cluster `B` -- 3 rows total, not deduplicated into 1
- [ ] A test proves one participant's votes are independent of another's: participant X casting `[A, A, A]` does not affect, replace, or merge with participant Y's own `[B]` submission on the same cycle -- both persist as separate rows, each keyed to its own participant
- [ ] A project member can view their own current vote allocation for a cycle (`GET /projects/{project_id}/cycles/{cycle_id}/votes/mine`), returning `200` with `{"cycle_id": ..., "cluster_ids": [...]}` for only the requester's own votes (as a multiset), and `{"cycle_id": ..., "cluster_ids": []}` if they have not voted -- this task adds no endpoint that exposes any other participant's votes or any cross-participant tally; that's #14's job
- [ ] Viewing own votes on a cycle whose `status` is `open` is rejected with `409` -- no cluster, and so no vote, can exist before reveal, mirroring #11's gating of its listing endpoint; viewing on a `revealed` or `closed` cycle returns `200`
- [ ] Casting or viewing votes on a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`
- [ ] Casting or viewing votes without a valid auth token is rejected with `401`
- [ ] Casting or viewing votes from an authenticated user with no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- Any endpoint that tallies, ranks, or reveals vote totals across participants, or hides/shows them based on voting progress -- that's #14's job, which depends on this task only for the underlying `Vote` rows to tally
- A facilitator (or any) "close voting" action -- also #14's job
- Any indication of who has or hasn't voted yet -- no follow-up filed since nothing before #14 needs it, and #14 owns deciding what "everyone has voted" means against this task's stored rows
- A frontend UI for voting -- consistent with #4-#12, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
- Re-validating a participant's earlier vote against clusters created or merged after that vote was cast -- each submission (including the replace on a second `POST`) is validated fresh against the cluster table at request time; #11's merge already moves cards, not votes, so no follow-up filed since nothing in the plan calls for cascading vote behavior on merge
Constraints:
- Depends on #11 (Manual clustering board) being merged: votes reference `Cluster` rows created there, and this task's `409`/`404` gating mirrors #11's pattern (`cycle.status == CycleStatus.REVEALED` required for the mutating endpoint, both `open` and `closed` rejected; the read endpoint stays open on `revealed` and `closed`, matching #11's cluster-listing endpoint)
- `Vote` currently has no participant/user reference (`app/models.py`: only `id`, `cluster_id`, `created_at`) -- add a `participant_id` column (FK to `users.id`, `nullable=False`) via a new Alembic migration, following the same `add_column` + `create_foreign_key` pattern as `migrations/versions/cda190f63753_add_author_id_to_feedback_cards.py`, which added `FeedbackCard.author_id` for #7. No unique constraint on `(participant_id, cluster_id)` -- stacking multiple votes on the same cluster is required behavior, not an error
- Endpoint shape: a single request carrying an array of 0-3 cluster ids, not one vote per request (up to 3 calls). This lets the endpoint validate and apply a participant's whole allocation atomically in one transaction -- reject the entire request on a `>3` count or an invalid cluster id, with no rows written -- instead of tracking a running per-participant vote count across multiple independent requests, which would need extra state and still be racy between calls
- Re-vote semantics: a second submission for the same cycle REPLACES the participant's previous votes for that cycle (delete-then-insert in one transaction), rather than being rejected as already-voted or added on top of the prior submission. Nothing in docs/tasks.md's or docs/plan.md's description of voting says it is one-shot, and replace-on-resubmit avoids needing a separate "change my vote" endpoint
- Use `require_project_member` (not `require_role("facilitator")`) for both routes: docs/plan.md's Roles section lists "Vote" under Team member, and this matches #11's and #12's Constraints that clustering-adjacent actions are open to any project member
- Add the two routes to a new `app/votes.py` router (`prefix="/projects"`), included from `app/main.py` the same way `app/cycles.py`, `app/cards.py`, and #11's `app/clusters.py` are, following the same Pydantic request/response, `Depends(get_db)`, `Depends(require_project_member)`, `HTTPException(status_code=..., detail=...)` pattern as #10/#11's routes
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 14. Reveal vote results after voting closes
Goal: While a revealed cycle's voting stays open, no one can see individual or running vote totals; once voting closes -- either because every project member has cast at least one non-empty ballot, or because the facilitator manually closes it -- any project member can view the final tally, ranked by total votes per cluster, which becomes the prioritized agenda #15 works from.
Acceptance Criteria:
- [ ] `GET /projects/{project_id}/cycles/{cycle_id}/votes/results` on a cycle whose `status` is `open` is rejected with `409` -- no clusters or votes can exist before reveal, mirroring #11's and #13's gating
- [ ] On a `revealed` cycle where `voting_closed` is `False` and at least one project member (of those holding a `ProjectMembership` on the project) has cast no non-empty ballot in this cycle, `GET .../votes/results` is rejected with `409` and the response body contains no cluster or vote data (e.g. `{"detail": "voting results are not available yet"}`)
- [ ] Once every project member with a `ProjectMembership` on the project has cast at least one `Vote` row on a cluster in this cycle, `GET .../votes/results` returns `200` with no explicit close action taken -- this is evaluated fresh on each call, not cached or persisted
- [ ] A test proves a participant who submits an empty `cluster_ids: []` ballot (a deliberate abstention, allowed by #13) leaves zero `Vote` rows and is NOT counted as having voted for the everyone-voted check -- if that participant is the only one who hasn't cast a non-empty ballot, `GET .../votes/results` stays `409` until the facilitator manually closes voting
- [ ] A facilitator can close voting early on a `revealed` cycle (`POST /projects/{project_id}/cycles/{cycle_id}/close-voting`), even if not every participant has voted; the response is `200` and includes `cycle_id`, `project_id`, and `voting_closed: true`
- [ ] After `close-voting`, `GET .../votes/results` returns `200` regardless of how many participants voted, including if zero participants voted
- [ ] A request to `close-voting` from a user who holds only the `team_member` role on that project is rejected with `403`, via `require_role("facilitator")`
- [ ] A request to `close-voting` on a cycle whose `status` is `open` or `closed` is rejected with `409`, and `voting_closed` is left unchanged
- [ ] A second call to `close-voting` on a cycle where `voting_closed` is already `True` is rejected with `409`, and `voting_closed` stays `True`
- [ ] Once results are available (via either path), `GET .../votes/results` returns clusters ranked by total vote count descending; a test with clusters holding 3, 1, and 0 votes confirms the response order is `[3-vote cluster, 1-vote cluster, 0-vote cluster]`
- [ ] Clusters with zero votes are included in the results (not omitted), each with `vote_count: 0`
- [ ] Two clusters with an equal vote count are ordered deterministically by ascending `cluster_id`; a test proves this tie-break
- [ ] Each entry in the results response includes `cluster_id`, `name`, and `vote_count`
- [ ] `GET .../votes/results` remains available (subject to the same `voting_closed`/everyone-voted gate) once the cycle's own `status` becomes `closed` -- consistent with #10's and #11's read endpoints staying accessible after the cycle itself closes
- [ ] `close-voting` and `votes/results` both reject a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, with `404`
- [ ] Both endpoints reject a request without a valid auth token with `401`
- [ ] Both endpoints reject a request from an authenticated user with no `ProjectMembership` on that project at all with `403`, not `500`
- [ ] `uv run pytest` passes, including the new tests
Out of scope:
- The vote-ranked discussion agenda itself, and marking topics Discussed/Skipped/Deferred -- that's #15's job, which consumes this task's ranked results but owns its own view and status transitions
- Preventing further vote submissions via #13's `POST .../votes` once voting has closed -- this task does not add a check to that endpoint, to avoid modifying code #13 owns (see Constraints); a vote cast after closing is reflected the next time `/votes/results` is called, since results are computed live rather than snapshotted at close time. No follow-up filed since nothing else in docs/tasks.md's plan requires the tally to be frozen once closed
- Surfacing in the results response *why* voting is available (manually closed vs. everyone voted) -- no follow-up filed since nothing in the plan calls for that distinction
- Reopening voting once closed -- no un-close endpoint is added; nothing in the plan calls for it, so no follow-up filed
- A frontend UI for the results view -- consistent with #4-#13, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
- Real-time push/notification when results become available -- nothing in the plan calls for it at this stage, so no follow-up filed
Constraints:
- Depends on #13 (Voting on discussion topics) being merged: `Vote.participant_id`, the `app/votes.py` router, and `require_project_member` already exist and must be reused, not re-derived
- Depends on #4 (Configurable roles and permissions) being merged: use the existing `require_role("facilitator")` dependency for `close-voting`
- Add a new `voting_closed: Mapped[bool] = mapped_column(Boolean, default=False)` column to `FeedbackCycle` in `app/models.py`. This is deliberately separate from `CycleStatus` (`open`/`revealed`/`closed`): voting has its own closed/open sub-state that lives inside the `revealed` cycle phase, distinct from the cycle-level transition to `closed` that #15 owns. Add it via a new Alembic migration (`add_column`, default `false`), following the same `batch_alter_table`/`add_column` pattern as the most recent prior migration at implementation time (e.g. #13's `Vote.participant_id` migration)
- "Everyone has voted" is computed lazily, at read time, inside `GET .../votes/results`: count of distinct `ProjectMembership.user_id` for the project, compared against the count of distinct `Vote.participant_id` values with at least one `Vote` row on a cluster belonging to this cycle. This check is deliberately NOT added to #13's `POST .../votes` endpoint (which would otherwise need to auto-close voting on submission) -- computing it lazily here avoids modifying code #13 owns, at the cost of the everyone-voted state only being reflected on the next call to `votes/results` rather than the instant the last vote is cast
- A participant who casts an empty `cluster_ids: []` ballot (a deliberate abstention, allowed by #13) leaves zero `Vote` rows and is therefore indistinguishable, for the everyone-voted check, from a participant who has not voted at all -- voting will never auto-close in a group where any member intentionally abstains this way; the facilitator's manual `close-voting` is the only way to proceed in that case. This is an accepted limitation of #13's existing data model (no separate "has submitted" marker), not something this task changes
- If a facilitator adds a new project member (#6) after every existing member has voted, the everyone-voted check re-evaluates against the new membership count on the next call and can flip `votes/results` back to `409` until the new member votes or the facilitator closes voting manually -- accepted as a consequence of the lazy, uncached computation
- Add both new routes (`POST .../close-voting`, `GET .../votes/results`) to the existing `app/votes.py` router (from #13), following the same Pydantic request/response, `Depends(get_db)`, `HTTPException(status_code=..., detail=...)` pattern as #13's routes; use `Depends(require_role("facilitator"))` for `close-voting` and `Depends(require_project_member)` for `votes/results`
- Gating mirrors the `409` pattern already used by #7/#10/#11/#13: `cycle.status == CycleStatus.OPEN` rejects both new endpoints with `409`; a `closed` cycle rejects `close-voting` with `409` (nothing left to close) but `votes/results` stays readable on `closed`, consistent with #10's and #11's read endpoints remaining available post-close
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 15. Discussion stage with topic status
Goal: The facilitator can step through a revealed cycle's topics (clusters) in vote-ranked order during the live meeting, marking each one Discussed, Skipped, or Deferred, and can close the cycle once discussion is done -- the action that finally moves a cycle out of `revealed` into `closed`, unblocking #18's meeting upload.
Depends on: #4 (Configurable roles and permissions), #14 (Reveal vote results after voting closes)
Acceptance Criteria:
- [ ] A facilitator can set a topic's discussion status on a revealed cycle (`PATCH /projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/discussion-status` with body `{"status": "discussed"}`); the `200` response includes `cluster_id`, `cycle_id`, and the updated `status`
- [ ] `status` accepts exactly `"discussed"`, `"skipped"`, or `"deferred"`; any other value -- including `"pending"`, since a topic cannot be manually reset back to not-yet-addressed -- is rejected with `422`, and the cluster's discussion status is left unchanged
- [ ] A cluster that has never had its discussion status set defaults to `pending`; a test proves this by reading it back via #14's `GET .../votes/results` (extended by this task, see below) before any status change is made
- [ ] Setting a topic's discussion status again (e.g. `discussed` -> `skipped`) overwrites the previous value; a test proves the final status is whichever was set last, with no history of prior values retained
- [ ] Setting a topic's discussion status on a cycle whose `status` is `open` or `closed` is rejected with `409`, and the cluster's discussion status is left unchanged
- [ ] Setting discussion status for a `cluster_id` that does not exist, or that belongs to a different `cycle_id`/`project_id` than the path, is rejected with `404`
- [ ] A request to set discussion status from a user who holds only the `team_member` role on that project is rejected with `403`, via the existing `require_role("facilitator")` dependency
- [ ] A request to set discussion status without a valid auth token is rejected with `401`
- [ ] A request to set discussion status from an authenticated user who has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] #14's `GET /projects/{project_id}/cycles/{cycle_id}/votes/results` response entries are extended by this task to include a `discussion_status` field alongside the existing `cluster_id`, `name`, and `vote_count` -- this task does not add a separate "list topics in vote order" endpoint (see Constraints)
- [ ] A test proves a discussion-status change is visible through `votes/results` immediately after the `PATCH` call, with no caching or staleness -- consistent with #14 computing its response live on every call
- [ ] The facilitator can close a revealed cycle (`POST /projects/{project_id}/cycles/{cycle_id}/close`); the `200` response includes the cycle's `id`, `project_id`, and updated `status`
- [ ] Closing a cycle whose `status` is `revealed` sets its `status` to `closed`; fetching the cycle afterward (`GET /projects/{project_id}/cycles/{cycle_id}`, from #6) reflects `closed`, not just the close response itself
- [ ] Closing a cycle whose `status` is `open` is rejected with `409` (it must be revealed first), and the cycle's `status` is left unchanged
- [ ] Closing a cycle whose `status` is already `closed` is rejected with `409`, and the cycle's `status` is left unchanged
- [ ] The facilitator can close a cycle regardless of how many topics have been marked Discussed/Skipped/Deferred, including when every topic is still `pending` -- closing is not gated on discussion completeness
- [ ] A request to close a cycle from a user who holds only the `team_member` role on that project is rejected with `403`, via `require_role("facilitator")`
- [ ] A request to close a cycle without a valid auth token is rejected with `401`
- [ ] A request to close a cycle from an authenticated user who has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] Closing a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`
- [ ] `uv run pytest` passes, including the new tests covering the cases above
Out of scope:
- Any historical log of discussion-status changes (who changed it, when, prior values) -- only the current status is needed by #16/#22 later in the plan; no follow-up filed
- Reverting a `closed` cycle back to `revealed` or `open` -- no un-close endpoint is added; nothing in the plan calls for it, matching #10's precedent of not filing a follow-up for un-revealing
- A dedicated "list topics in vote order" endpoint -- this task deliberately extends and reuses #14's `GET .../votes/results` instead of duplicating its vote-ranked ordering/query logic (see Constraints)
- Real-time push/websocket notification of status changes to other viewers -- this project has no real-time infrastructure anywhere in the backlog; "reflected immediately" here means a fresh `GET` call returns the current value, not a live push, consistent with #14 leaving the same out of scope for its own results endpoint. No follow-up filed since nothing in docs/plan.md calls for push notifications
- Free-text notes, decisions, or action items tied to a topic during discussion -- that's #16's job, which depends on this task for the discussion view to exist
- Requiring every topic to be marked before the cycle can close -- deliberately not required (see Acceptance Criteria); no follow-up filed since nothing in the plan calls for it
- A frontend UI for the discussion view -- consistent with #4-#14, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
Constraints:
- Depends on #4 (Configurable roles and permissions) and #14 (Reveal vote results after voting closes) being merged: both are groomed but not yet implemented as of this task being groomed -- this task's routes and migration cannot be written, and #14's response model cannot be extended, until #14's actual merged code exists. Build against #14's real endpoint, not a re-derived one
- Add a new `DiscussionStatus(str, enum.Enum)` to `app/models.py` with values `PENDING`, `DISCUSSED`, `SKIPPED`, `DEFERRED` (matching the existing `str, enum.Enum` + lowercase-value style of `CycleStatus`/`CardCategory`/`ActionStatus`), and add `discussion_status: Mapped[DiscussionStatus] = mapped_column(Enum(DiscussionStatus), default=DiscussionStatus.PENDING)` to `Cluster` in `app/models.py`
- Generate the schema change via `uv run alembic revision --autogenerate -m "..."`, matching the existing files in `migrations/versions/` (`cda190f63753_add_author_id_to_feedback_cards.py` is the most recent as of this writing; #13's and #14's own migrations will already exist by the time this task starts, per Depends on -- follow whichever file is newest at implementation time)
- No new migration is needed for the cycle-close transition itself -- `FeedbackCycle.status` and `CycleStatus.CLOSED` already exist in `app/models.py` (from #2); this task only adds the route that performs the `REVEALED` -> `CLOSED` write
- Add the discussion-status route to a new `app/discussion.py` router (or extend #14's `app/votes.py` -- implementer's choice, matching the existing router-per-resource pattern), included from `app/main.py` the same way the other routers are
- Add the close-cycle route to the existing `app/cycles.py` router, alongside `create_cycle`, `get_cycle`, and `reveal_cycle`, following the same Pydantic request/response, `Depends(get_db)`, `Depends(require_role("facilitator"))`, `HTTPException(status_code=..., detail=...)` pattern already used by `reveal_cycle`
- Don't add a separate "list topics in vote order" endpoint: extend #14's `GET .../votes/results` response entries to also include `discussion_status` (alongside the existing `cluster_id`, `name`, `vote_count`) -- #14 already computes and returns clusters in vote-ranked order, and a second endpoint duplicating that ordering/query logic would just be two sources of truth for the same list. This task's own new endpoints are write-only: set a topic's status, and close the cycle
- Every mutating endpoint in this task must gate on cycle status: the discussion-status `PATCH` requires `cycle.status == CycleStatus.REVEALED`, rejecting both `open` and `closed` with `409`; the close `POST` requires `cycle.status == CycleStatus.REVEALED` too, rejecting both `open` (nothing to close yet) and already-`closed` with `409` -- mirroring the gating pattern already used by #7/#10/#11/#13/#14
- Use `require_role("facilitator")` (not `require_project_member`) for both new endpoints -- per docs/plan.md's "Run the discussion" step (facilitator marks each topic) and its Facilitator role list ("Create and close feedback cycles", "Control the retrospective stages"), both actions in this task are facilitator-only, distinct from clustering/voting which #11/#12/#13 already opened to any project member
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 16. Record notes, decisions, and action items during discussion
Goal: During a revealed cycle's live discussion, any project member can record a free-text note, a decision, or an action item against a specific topic (cluster) by passing that cluster's id explicitly in each request -- no server-side "currently being discussed" state is introduced -- and every participant can read those entries back immediately with a fresh `GET`. This is manual, live entry, distinct from the later AI-draft extraction path (#20/#21) that writes to the same `Decision`/`ActionItem` tables from an uploaded meeting recording.
Depends on: #15 (Discussion stage with topic status), #11 (Manual clustering board)
Acceptance Criteria:
- [ ] Any project member (`team_member` or `facilitator`) can add a free-text note tied to a topic (`POST /projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/notes` with body `{"text": "..."}`); the `201` response includes `id`, `cycle_id`, `cluster_id`, `text`, `author_id` (the requester's own id), and `created_at`
- [ ] Submitting a blank or whitespace-only `text` is rejected with `422`, and no `DiscussionNote` row is created
- [ ] Any project member can add a decision tied to a topic (`POST /projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/decisions` with body `{"description": "..."}`); the `201` response includes `id`, `cycle_id`, `cluster_id`, `description`, `author_id` (the requester's own id), `confirmed: true`, and `created_at` -- `confirmed` is always `true` for a manually-entered decision, since this is live entry by someone in the meeting, not an unconfirmed AI draft (contrast #20/#21)
- [ ] Submitting a blank or whitespace-only `description` for a decision is rejected with `422`, and no `Decision` row is created
- [ ] Any project member can add an action item tied to a topic (`POST /projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/action-items` with body `{"description": "...", "due_date": "YYYY-MM-DD" (optional), "owner_id": <int> (optional)}`); the `201` response includes `id`, `cycle_id`, `cluster_id`, `description`, `due_date` (`null` if omitted), `status: "open"`, `owner_id`, `confirmed: true`, and `created_at`
- [ ] Omitting `owner_id` on action-item creation defaults the owner to the requester themselves; a test proves the response's `owner_id` equals the creator's own id
- [ ] Supplying an explicit `owner_id` for a different project member (e.g. someone records "Bob will do X" during the meeting) sets that member as the owner; a test proves the response's `owner_id` is the named member, not the creator
- [ ] Supplying an `owner_id` for a user who does not exist, or who exists but has no `ProjectMembership` on this `project_id`, is rejected with `404`, and no `ActionItem` row is created
- [ ] Submitting a blank or whitespace-only `description` for an action item is rejected with `422`, and no `ActionItem` row is created
- [ ] Submitting a `due_date` that is not a valid ISO date is rejected with `422`
- [ ] A newly created action item always starts with `status: "open"` -- the create endpoint has no way to set `status` directly at creation; changing it afterward is #17's job
- [ ] Each of the three creation endpoints requires `cycle.status == CycleStatus.REVEALED`; a cycle in `open` or `closed` status is rejected with `409`, and no row is created
- [ ] Each of the three creation endpoints rejects a `cluster_id` that does not exist, or that belongs to a different `cycle_id`/`project_id` than the path, with `404`
- [ ] Any project member can list a topic's notes (`GET /projects/{project_id}/cycles/{cycle_id}/clusters/{cluster_id}/notes`), decisions (`GET .../clusters/{cluster_id}/decisions`), and action items (`GET .../clusters/{cluster_id}/action-items`); each entry includes the same fields as its creation response, ordered by `created_at` ascending
- [ ] Listing notes/decisions/action items on a cycle whose `status` is `open` is rejected with `409` -- no cluster, and so no note/decision/action item, can exist before reveal, mirroring #11's cluster-listing gating
- [ ] Listing notes/decisions/action items on a `revealed` or `closed` cycle returns `200` -- entries recorded while `revealed` remain readable after the cycle is later closed via #15's close endpoint
- [ ] A test proves an entry created via one of the three `POST` endpoints appears in the corresponding `GET` listing on an immediately subsequent call, with no caching or staleness -- this is how "visible to other participants without a page reload" is satisfied (a fresh `GET` reflecting current state), consistent with #14/#15's own scoping, not a live push
- [ ] A test proves the design is stateless with respect to "current topic": two notes created back-to-back against two different `cluster_id`s in the same cycle, in either order, each land on the correct cluster -- there is no server-side concept of which topic is "current" that a request could omit or get wrong
- [ ] Each of the six endpoints (3 create, 3 list) rejects a request without a valid auth token with `401`
- [ ] Each of the six endpoints rejects a request from an authenticated user with no `ProjectMembership` on that project at all with `403`, not `500`
- [ ] Each of the six endpoints rejects a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, with `404`
- [ ] `uv run pytest` passes, including the new tests covering the cases above
Out of scope:
- Editing or deleting a note, decision, or action item after creation -- no endpoints added; nothing in docs/plan.md calls for correcting a mis-typed entry, no follow-up filed
- Changing an action item's `status` (Open/Done) -- that's #17's job (already groomed), which depends on this task for the `ActionItem` rows to exist
- A project-wide or cycle-wide listing of action items across all of a cycle's clusters -- that's #17's job ("A project-level view lists all action items with description, owner, due date, status, and related topic"); this task only adds per-topic listings
- The AI-draft extraction path that writes unconfirmed `Decision`/`ActionItem` rows from an uploaded transcript -- that's #20/#21's job, a second path into the same tables this task adds `author_id`/`owner_id` to; #20/#21 must decide how those columns get populated for an AI-drafted row (e.g. left until the confirming facilitator, or some other rule) -- out of scope here since no transcript/AI pipeline exists yet
- Server-side "current topic being discussed" state on `FeedbackCycle` (e.g. a `current_cluster_id` pointer) -- deliberately not added; see Constraints
- Real-time push/websocket delivery of new entries to other viewers -- no push infrastructure anywhere in this backlog; "visible ... without a page reload" is satisfied by a fresh `GET` reflecting current state live (see Acceptance Criteria), consistent with #14/#15's own scoping. No follow-up filed since nothing in docs/plan.md calls for push notifications
- A frontend UI for the discussion view -- consistent with #4-#15, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
- Changing #11's cluster-merge endpoint to move, protect, or explicitly cascade-delete notes/decisions/action items when a source cluster is merged away -- #11 explicitly deferred this decision to #13 and #16 in its own grooming, but #11 is being implemented independently of this task, and this task's scope is restricted to the create/list endpoints and the new `author_id`/`owner_id` columns, not `app/clusters.py`. Flagging rather than silently dropping: `Cluster.notes` already cascades with `delete-orphan` (from #2), so if #11's merge ever deletes the source `Cluster` row, its notes are destroyed silently; `Cluster.decisions`/`Cluster.action_items` have no such cascade and `Decision.cluster_id`/`ActionItem.cluster_id` are nullable, so the same deletion would either orphan those rows (a dangling `cluster_id`) or fail outright on a database that enforces foreign keys, depending on how #11's merge is actually implemented. No follow-up issue filed as part of this grooming pass; whoever next touches merge or these tables should file one
Constraints:
- Add `author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)` to `DiscussionNote` and to `Decision`, and `owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)` to `ActionItem`, in `app/models.py` -- following the same pattern as `Vote.participant_id` (#13, already groomed) and `FeedbackCard.author_id` (#7, already merged; see `migrations/versions/cda190f63753_add_author_id_to_feedback_cards.py`)
- Generate the schema change via a single `uv run alembic revision --autogenerate -m "..."` adding all three columns, following the same `add_column` + `create_foreign_key` pattern as `migrations/versions/cda190f63753_add_author_id_to_feedback_cards.py`; follow whichever file is newest in `migrations/versions/` at implementation time (#11's, #13's, #14's, and #15's own migrations will already exist by then, per Depends on)
- No "current topic" concept is added to `FeedbackCycle`. The client passes `cluster_id` explicitly in the URL of every note/decision/action-item request. This keeps the feature stateless: no new mutable pointer on `FeedbackCycle` for a facilitator to keep in sync while stepping through topics via #15's discussion-status endpoint, and no risk of an entry landing on the wrong topic because a server-side "current" pointer was stale or got moved by someone else mid-meeting. #15's discussion-status `PATCH` already tracks per-cluster progress (pending/discussed/skipped/deferred); this task does not duplicate that as a second, cycle-level "current" pointer
- Use `require_project_member` (not `require_role("facilitator")`) for all six endpoints -- per docs/plan.md's "Run the discussion" step ("Team members can manually record notes, decisions, and action items during the meeting") and this issue's original scope ("any team member"), matching #11/#12/#13's precedent that discussion/clustering-adjacent actions are open to any project member, not facilitator-only
- `confirmed` is hardcoded to `true` in the creation handler for both decisions and action items -- never accepted from the request body -- since a manually-entered-during-the-meeting row needs no separate approval step, contrasting with #20/#21's AI-draft path which must create rows with `confirmed: false` pending facilitator review
- `owner_id` on action-item creation: accept an optional field in the request body; default to `current_user.id` when omitted; when provided, validate it is the `user_id` of an existing `ProjectMembership` on this `project_id` (`404` otherwise, mirroring the existing "does not exist, or belongs to a different X" `404` convention used by #11/#13/#15) -- per docs/plan.md's Action item section, which lists "Owner" as a field distinct from who recorded it, since the recorder and the owner are not necessarily the same person in a live meeting
- Notes and decisions do not accept an author override in the request body -- `author_id` is always `current_user.id`; only action items separate "who recorded it" (the creator) from "who owns it" (`owner_id`), per docs/plan.md's Action item section
- Depends on #15 (Discussion stage with topic status) being merged for the `REVEALED` cycle-status gating this task reuses, and #11 (Manual clustering board) being merged for the `Cluster` rows these entries attach to -- both are groomed but not yet implemented (#11 is in progress) as of this task being groomed; this task's routes and migration cannot be written until their actual merged code exists
- Add the notes/decisions/action-items routes to `app/discussion.py` if #15's implementer created that router for the discussion-status endpoint (per #15's own Constraints); if #15's implementer instead extended `app/votes.py`, add these routes there instead -- match wherever #15's discussion-status endpoint actually landed, not a re-derived location
- Every creation endpoint requires `cycle.status == CycleStatus.REVEALED`, rejecting both `open` and `closed` with `409`; every listing endpoint requires `cycle.status != CycleStatus.OPEN`, rejecting `open` with `409` and allowing both `revealed` and `closed` -- mirroring #11's cluster-listing gating pattern
- Follow the existing FastAPI/Pydantic pattern already used by `app/cycles.py`, `app/cards.py`: Pydantic request/response models, `response_model=...`, `Depends(get_db)`, `Depends(require_project_member)`, `HTTPException(status_code=..., detail=...)`
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`

## 17. Action item tracking and status updates
Goal: Once an action item exists (created manually via #16, or -- once #20/#21 land -- confirmed from an AI-drafted extraction), any project member can see it in a single project-wide list spanning every cycle in the project, showing its description, owner, due date, status, and related topic; and only the assigned owner can mark their own action item Done, so outcomes stay tracked after the meeting that produced them ends.
Depends on: #3 (User authentication), #16 (Record notes, decisions, and action items during discussion)
Acceptance Criteria:
- [ ] Any project member (`team_member` or `facilitator`) can list every action item in a project in one call (`GET /projects/{project_id}/action-items`); the `200` response is a list, each entry containing `id`, `cycle_id`, `cluster_id`, `cluster_name` (the related topic's name, `null` if the cluster has no name), `description`, `owner_id`, `due_date` (`null` if none), `status`, and `created_at`
- [ ] A test proves the list spans every cycle in the project, not just one: action items recorded against two different cycles in the same project both appear in a single call to `GET /projects/{project_id}/action-items`
- [ ] A project with no action items yet returns `200` with an empty list, not `404`
- [ ] Only `ActionItem` rows with `confirmed == true` are included in the list; a test creates an `ActionItem` with `confirmed = False` directly (no live endpoint produces one yet -- see Constraints) and proves it is absent from the response while a `confirmed = True` row in the same project appears
- [ ] The list is ordered by `created_at` ascending, matching #16's listing convention
- [ ] Listing without a valid auth token is rejected with `401`
- [ ] Listing from an authenticated user with no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] The action item's assigned owner can mark their own action item Done (`PATCH /projects/{project_id}/action-items/{action_item_id}` with body `{"status": "done"}`); the `200` response reflects the updated `status`, and a subsequent `GET /projects/{project_id}/action-items` call shows the same updated value
- [ ] `status` accepts exactly `"open"` or `"done"`; any other value is rejected with `422`, and the item's `status` is left unchanged
- [ ] A test proves the owner can also move their own action item from `done` back to `open` through the same endpoint -- the transition is not restricted to one-way Open -> Done (see Constraints)
- [ ] A user who is authenticated and holds a `ProjectMembership` on the project, but whose id does not equal the action item's `owner_id`, is rejected with `403` when calling the status-update endpoint -- including a `facilitator`, who gets no special override; the item's `status` is left unchanged
- [ ] The request body's `description`, `owner_id`, and `due_date` are not editable through this endpoint; a test sends all three alongside `status` in one request and proves only `status` changed in the response and in a subsequent `GET`
- [ ] Updating status on an `action_item_id` that does not exist is rejected with `404`
- [ ] Updating status on an `action_item_id` that exists but whose cycle belongs to a different `project_id` than the one in the path is rejected with `404`
- [ ] Updating status on an `action_item_id` that resolves to a `confirmed == false` row is rejected with `404` -- an unconfirmed row is invisible to this task's own list endpoint, so it is not reachable to update either (forward-looking; see Constraints)
- [ ] Updating status without a valid auth token is rejected with `401`
- [ ] Updating status from an authenticated user with no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] `uv run pytest` passes, including the new tests covering the cases above
Out of scope:
- Editing an action item's `description`, `owner_id`, or `due_date` after creation -- nothing in docs/plan.md calls for correcting or reassigning an action item after it's recorded; no follow-up filed, matching #16's own precedent for not filing one on the equivalent question for notes/decisions
- A facilitator (or anyone other than the assigned owner) marking someone else's action item Done -- explicitly excluded per docs/plan.md's Team member role ("Update actions assigned to them"); no role-based override exists anywhere in this task
- Deleting an action item -- consistent with the rest of this backlog, nothing calls for delete on any of these entities; no follow-up filed
- Surfacing unconfirmed AI-drafted action items (from the future #20/#21 path) anywhere in this project-level view -- this task's `confirmed == true` filter already keeps them out once #20/#21 exist and start writing `confirmed = false` rows; no separate follow-up needed since #21 (Facilitator review and confirmation of extracted items) already owns turning those into `confirmed = true` rows
- Filtering or sorting the project-level list by status, owner, or cycle (e.g. "my action items", "open only") -- this task builds one unfiltered list; #23 (Project dashboard, already groomed) depends on this task for "a list of the project's open action items" and can filter this endpoint's response when it's implemented, so no follow-up filed
- A frontend UI for either endpoint -- consistent with #4-#16, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
Constraints:
- `ActionItem` already has `cycle_id`, nullable `cluster_id`, `description`, nullable `due_date`, `status` (`ActionStatus` enum: `OPEN`/`DONE`, default `OPEN`), `confirmed`, and `created_at` in `app/models.py`. #16 (groomed, not yet merged) adds `owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)` to this same model -- this task depends on that column already existing on the merged model and must not re-add or duplicate it via its own migration. No new columns or migration are needed by this task itself
- Depends on #16 (Record notes, decisions, and action items during discussion) being merged, for `ActionItem.owner_id` and the `confirmed = true` manually-entered rows this task lists; #16 is groomed but not yet implemented as of this grooming pass -- this task's routes cannot be written until #16's actual merged code exists. Depends on #3 (User authentication) only for `get_current_user`/token auth, already merged
- Use `require_project_member` (not `require_role`) as the base dependency for both new endpoints -- listing is available to any project member per docs/plan.md's Project page ("Open action items") and its Team member role list; the status-update endpoint additionally checks `current_user.id == action_item.owner_id` inside the handler, after the membership dependency succeeds
- The status-update endpoint returns `403`, not `404`, when the requester holds project membership but isn't the item's owner. This differs from #9's card-edit-by-non-owner, which uses `404` to avoid confirming a card the requester has no other visibility into exists -- an action item doesn't have that problem, since it's already visible project-wide via this task's own list endpoint, so a `404` here would hide nothing the requester couldn't already see. `403` states plainly that the item exists but isn't theirs to change
- The listing endpoint filters to `ActionItem.confirmed == True` only. No currently-implemented or currently-groomed creation path produces a `confirmed = False` row (#16 hardcodes `confirmed = true` for its manual entry), so this filter has no observable effect today -- it exists so a future unconfirmed AI-drafted row from #20/#21 doesn't leak into a team-facing list before the facilitator confirms it, per #20's own "nothing here is shown to the team as final yet" rule. Flagged as forward-looking since #20/#21 don't exist yet: the test for this filter must create the `confirmed = False` row directly via the ORM/test fixture, not through any live endpoint
- The status-update endpoint applies the same `confirmed == True` check: an `action_item_id` resolving to a `confirmed = False` row is rejected with `404`, for the same forward-looking reason as the list filter above
- `status` accepts either `ActionStatus` value (`"open"` or `"done"`); nothing in docs/plan.md restricts the transition to one-way Open -> Done, and `ActionStatus` (from #2) has no other states to guard against, so this task allows and tests reopening a `done` item through the same endpoint, gated only by the existing ownership check
- Resolve an `ActionItem`'s project via its cycle (`ActionItem.cycle_id -> FeedbackCycle.project_id`) and compare against the `project_id` path parameter, for both endpoints; an `action_item_id` that exists but belongs to a different project's cycle is rejected with `404`, mirroring the "belongs to a different project" `404` convention used throughout #7/#9/#11/#13/#15/#16
- Add both routes to a new `app/action_items.py` router (`prefix="/projects"`), included from `app/main.py` the same way the other routers are -- distinct from wherever #16 landed its per-topic, per-cluster notes/decisions/action-items routes (`app/discussion.py` or `app/votes.py`, per #16's own Constraints), since this task's two endpoints are project-scoped, not cycle/cluster-scoped
- Follow the existing FastAPI/Pydantic pattern in `app/cycles.py`, `app/cards.py`, `app/votes.py`: Pydantic request/response models, `response_model=...`, `Depends(get_db)`, `Depends(require_project_member)`, `HTTPException(status_code=..., detail=...)`
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`; since this task depends on #16's actual merged `ActionItem.owner_id` and creation endpoints, tests may create `ActionItem` rows directly via the ORM for fixture setup where convenient, consistent with how earlier tasks' tests seed data outside the endpoint under test

## 18. Meeting upload page
Goal: A facilitator can attach a record of a closed cycle's meeting -- audio, video, a transcript file, or pasted transcript text -- through a single upload endpoint that persists it as a new `MeetingUpload` row in `pending` status, and any project member can then list a cycle's uploads to see each one's status. This task covers accepting and storing the upload only; no transcription or processing happens here -- that's #19's job, which drives `status` through `processing`/`complete`/`failed` and populates `transcript_text` for audio/video.
Depends on: #4 (Configurable roles and permissions), #6 (Create a feedback cycle and invite the team), #15 (Discussion stage with topic status)
Acceptance Criteria:
- [ ] A facilitator can upload a meeting record against a closed cycle (`POST /projects/{project_id}/cycles/{cycle_id}/uploads`), sending a `multipart/form-data` body with a `kind` field (`"audio"`, `"video"`, `"transcript_file"`, or `"transcript_text"`) plus either a `file` (for `audio`/`video`/`transcript_file`) or a `transcript_text` string field (for `transcript_text`); the `201` response includes the new upload's `id`, `cycle_id`, `kind`, `status`, `transcript_text` (`null` unless `kind == "transcript_text"`), and `created_at`
- [ ] A newly created upload's `status` is always `"pending"`, regardless of `kind` -- this task never sets `"processing"`, `"complete"`, or `"failed"`
- [ ] Uploading `kind: "audio"` or `kind: "video"` with a non-empty `file` stores the file's bytes to a local `uploads/` directory on disk and creates the row with `status: "pending"` and `transcript_text: null`
- [ ] Uploading `kind: "transcript_file"` with a non-empty `file` behaves the same as audio/video: the file is stored to disk and the row is created with `transcript_text: null`
- [ ] Uploading `kind: "transcript_text"` with a non-blank `transcript_text` field creates the row with the submitted text stored directly in `transcript_text`, and writes no file to disk
- [ ] Uploading `kind: "audio"`, `"video"`, or `"transcript_file"` with no `file`, or an empty file, is rejected with `422`, and no `MeetingUpload` row is created
- [ ] Uploading `kind: "transcript_text"` with no `transcript_text` field, or a blank/whitespace-only value, is rejected with `422`, and no `MeetingUpload` row is created
- [ ] Uploading with both a non-empty `file` and a non-blank `transcript_text` in the same request is rejected with `422`, and no `MeetingUpload` row is created -- exactly one input is accepted per request
- [ ] Uploading with a `kind` value outside the four accepted values is rejected with `422`
- [ ] Uploading against a cycle whose `status` is `open` or `revealed` is rejected with `409`, and no `MeetingUpload` row is created -- only a `closed` cycle accepts uploads
- [ ] A request to upload from a user who holds only the `team_member` role on that project is rejected with `403`, via `require_role("facilitator")`
- [ ] A request to upload without a valid auth token is rejected with `401`
- [ ] A request to upload from a user who is authenticated but has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] Uploading against a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`
- [ ] A cycle can have more than one upload: a test uploads a `transcript_text` entry and an `audio` entry against the same closed cycle, and both persist as separate `MeetingUpload` rows, both returned by the list endpoint below
- [ ] Any project member (`team_member` or `facilitator`) can list a cycle's uploads (`GET /projects/{project_id}/cycles/{cycle_id}/uploads`); the `200` response is a list, each entry with the same fields as the creation response (`id`, `cycle_id`, `kind`, `status`, `transcript_text`, `created_at`), ordered by `created_at` ascending
- [ ] A cycle with no uploads yet -- including one that is still `open` or `revealed`, since no upload can exist before it is closed -- returns `200` with an empty list, not `404` or `409`
- [ ] A request to list uploads without a valid auth token is rejected with `401`
- [ ] A request to list uploads from an authenticated user with no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] Listing uploads for a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`
- [ ] `uv run pytest` passes, including the new tests covering the cases above
Out of scope:
- Any transcription, AI extraction, or status transition off `pending` (to `processing`/`complete`/`failed`) -- that's #19 (Transcript generation from uploaded audio/video)'s job, which drives `MeetingUpload.status` forward and populates `transcript_text` for audio/video uploads; this task only ever creates rows in `pending`
- Auto-completing a `transcript_text` upload immediately, even though pasted text technically needs no transcription -- kept as `pending` like every other kind, for consistency and to avoid this task making a status-transition decision that belongs to #19; no follow-up filed since #19 already owns every transition off `pending` and can special-case `transcript_text` there if desired
- Deleting or replacing an upload -- consistent with the rest of this backlog, nothing calls for delete on any entity here; a facilitator who uploads the wrong thing simply uploads again, which this task's multiple-uploads-per-cycle acceptance criterion already supports. No follow-up filed
- Enforcing a file-size limit, validating a file's content-type against its declared `kind`, or virus/malware scanning an uploaded file -- nothing in docs/plan.md calls for this; no follow-up filed since addressing it properly likely first needs the object-storage migration this task's Constraints already flag as out of scope
- Cloud/object storage (e.g. S3) for uploaded files -- this project has no such infrastructure anywhere in its code or dependencies; see Constraints for the local-disk decision this task makes instead and the reasoning
- Downloading or serving a previously uploaded file's bytes back out through the API -- no task in docs/tasks.md's plan calls for retrieving the raw file after upload; #19 reads `file_path` directly from the database row for its own processing, not through an API endpoint. No follow-up filed
- A frontend UI for the upload or status page -- consistent with #4-#17, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
Constraints:
- Add a new `MeetingUpload` model to `app/models.py`, following the existing SQLAlchemy 2.0 declarative style (`Mapped[...]` / `mapped_column`, `relationship(back_populates=...)`): `id`, `cycle_id: Mapped[int] = mapped_column(ForeignKey("feedback_cycles.id"))`, `kind: Mapped[UploadKind] = mapped_column(Enum(UploadKind))`, `status: Mapped[UploadStatus] = mapped_column(Enum(UploadStatus), default=UploadStatus.PENDING)`, `transcript_text: Mapped[str | None] = mapped_column(Text)`, `file_path: Mapped[str | None] = mapped_column(String(500))`, `created_at`. The relationship is one-to-many (a cycle can have multiple upload attempts, e.g. a retry after a bad file) -- add a `FeedbackCycle.uploads` relationship with `cascade="all, delete-orphan"`, matching the existing pattern for `cards`/`clusters`/`decisions`/`action_items`
- Add two new `str, enum.Enum` classes to `app/models.py`, matching the existing lowercase-value style of `CycleStatus`/`CardCategory`/`ActionStatus`: `UploadKind` (`AUDIO`, `VIDEO`, `TRANSCRIPT_FILE`, `TRANSCRIPT_TEXT`) and `UploadStatus` (`PENDING`, `PROCESSING`, `COMPLETE`, `FAILED`)
- Generate the schema change via `uv run alembic revision --autogenerate -m "..."`, matching the existing files in `migrations/versions/` -- follow whichever file is newest at implementation time (`cda190f63753_add_author_id_to_feedback_cards.py` is the most recent as of this grooming pass; several of #11/#13/#14/#15/#16/#17's own migrations may already exist by the time this task starts, per their own Depends on notes)
- File storage: this project has no object storage/cloud storage configured anywhere (no S3 client, no storage config in `app/`, nothing in `pyproject.toml`), and audio/video files can be large, so store an uploaded `file` directly to a local `uploads/` directory on disk (e.g. `uploads/{cycle_id}/{upload_id-or-uuid}_{original filename}`), saving only the resulting path on `MeetingUpload.file_path`. This is an explicit MVP simplification: a real deployment would eventually need object storage instead, but adding that infrastructure is out of scope for this task and nothing in the current backlog calls for it yet
- The `uploads/` directory this task introduces is written to at runtime and must not be committed -- add `uploads/` to `.gitignore` (alongside the existing `*.db` entry) as part of implementing this task
- `python-multipart` is required by FastAPI to parse `UploadFile`/`Form` multipart request bodies and is not currently in `pyproject.toml`'s `dependencies` -- add it (e.g. `uv add python-multipart`) as part of this task
- The `POST` endpoint's request body must be parsed with FastAPI's `UploadFile` and `Form(...)` parameters (not a Pydantic `BaseModel`), since it accepts `multipart/form-data`: `kind` and `transcript_text` as `Form(...)` fields (the latter optional), `file` as an optional `UploadFile`. Validate in the handler that exactly one of (a non-empty `file` matching `kind` for `audio`/`video`/`transcript_file`) or (a non-blank `transcript_text` for `kind: "transcript_text"`) is present, raising `HTTPException(422, ...)` otherwise. Use a Pydantic response model (`response_model=...`) for the response even though the request itself isn't one
- Use `Depends(require_role("facilitator"))` for the `POST` route and `Depends(require_project_member)` for the `GET` route, following #6's and #10's precedent that creating/mutating a cycle-scoped resource is facilitator-only while reading it is open to any project member
- The `POST` route requires `cycle.status == CycleStatus.CLOSED`, rejecting both `open` and `revealed` with `409`, mirroring the `409`-gating pattern already used by #7/#10/#11/#13/#14/#15/#16 for their own cycle-status checks; the `GET` route applies no status gate at all -- an `open`/`revealed` cycle simply has no uploads yet, so it returns an empty list rather than a `409`
- Add both routes to a new `app/uploads.py` router (`prefix="/projects"`), included from `app/main.py` the same way the other routers are
- Follow the existing FastAPI pattern in `app/cycles.py` for cycle lookup/404 (a `cycle_id` that does not exist, or that belongs to a different `project_id`, both `404`) and `HTTPException(status_code=..., detail=...)` for errors
- Depends on #4 (Configurable roles and permissions) and #6 (Create a feedback cycle and invite the team), both already merged: `require_role`, `require_project_member`, and `FeedbackCycle` already exist in `app/security.py`/`app/models.py` and must be reused, not re-derived
- Depends on #15 (Discussion stage with topic status) being merged for its `POST .../close` endpoint -- that endpoint is the only way any cycle in this backlog reaches `closed`. #15 is groomed but not yet implemented as of this grooming pass; if it is still unmerged when this task starts, tests needing a closed cycle may seed one directly via the ORM (`status=CycleStatus.CLOSED`), consistent with how other tasks' tests seed prerequisite state ahead of their real dependency landing
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`; use `client.post(..., files={"file": (filename, bytes, content_type)}, data={"kind": "audio"})` for file-upload tests and `client.post(..., data={"kind": "transcript_text", "transcript_text": "..."})` (no `files`) for the pasted-text tests

## 19. Transcript generation from uploaded audio/video
Goal: A facilitator can trigger transcription for a specific `pending` `audio`/`video` `MeetingUpload` (`POST /projects/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/transcribe`), driving its `status` through `processing` to `complete` (with `transcript_text` populated) or `failed`, via a single mockable transcription function -- so this task never needs an AI/SDK dependency and no test ever makes a real network call. This endpoint only ever transcribes `audio`/`video` uploads: a `transcript_file` or `transcript_text` upload is a different concern (see Out of scope) and this task rejects calling it on either.
Depends on: #18 (Meeting upload page)
Acceptance Criteria:
- [ ] A facilitator can trigger transcription for a `pending` `audio` or `video` upload (`POST /projects/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/transcribe`); when the injectable transcription function returns a transcript string, the endpoint sets the upload's `status` to `complete` and `transcript_text` to the returned string, and responds `200` with the updated upload (`id`, `cycle_id`, `kind`, `status`, `transcript_text`, `created_at`), using #18's existing response shape
- [ ] When the injectable transcription function raises (simulating a failed or timed-out provider call), the endpoint catches the exception, sets the upload's `status` to `failed`, leaves `transcript_text` as `null`, and still responds `200` (never `4xx`/`5xx` for a provider failure) with the upload reflecting `status: "failed"`
- [ ] A test stubs/monkeypatches the transcription function to return a fixed transcript string and asserts the "complete" behavior above, with no real network call and no API key/credential present
- [ ] A test stubs/monkeypatches the transcription function to raise and asserts the "failed" behavior above, again with no real network call
- [ ] A fresh `GET /projects/{project_id}/cycles/{cycle_id}/uploads` (#18) after a successful or failed transcription reflects the updated `status` and `transcript_text` for that upload -- suggestions persist through #18's existing list endpoint; no new read endpoint is added by this task
- [ ] Calling transcribe on an upload whose `kind` is `transcript_file` or `transcript_text` is rejected with `422`, and the upload's `status`/`transcript_text` are left unchanged -- this endpoint only ever transcribes `audio`/`video` uploads
- [ ] Calling transcribe on an upload whose `status` is `processing`, `complete`, or `failed` (i.e. not `pending`) is rejected with `409`, and the upload's fields are left unchanged -- including calling it a second time on an upload this same task already moved to `complete` or `failed`
- [ ] Calling transcribe on an `upload_id` that does not exist, or that belongs to a different `cycle_id`/`project_id` than the ones in the path, is rejected with `404`
- [ ] Calling transcribe on a `cycle_id` that does not exist, or that belongs to a different `project_id` than the one in the path, is rejected with `404`, matching #18's existing cycle-lookup pattern
- [ ] A request to transcribe from a user who holds only the `team_member` role on that project is rejected with `403`, via `require_role("facilitator")`
- [ ] A request to transcribe without a valid auth token is rejected with `401`
- [ ] A request to transcribe from a user who is authenticated but has no `ProjectMembership` on that project at all is rejected with `403`, not `500`
- [ ] `uv run pytest` passes, including the new tests covering the cases above
Out of scope:
- Transcribing or extracting text for `transcript_file` uploads (reading a previously-uploaded transcript file's bytes on disk into `transcript_text`) -- a different concern (file text extraction, not audio/video transcription) than this task's title covers. #18 stores a `transcript_file` upload's bytes to disk but leaves `transcript_text: null` and `status: "pending"`, and nothing in the current backlog ever advances either. This is a real gap: worth a follow-up issue when it's picked up, not filed here since it sits outside this task's audio/video-only mandate (mirrors the same "no task owns this yet" pattern #6's grooming used for the cycle's `open`-to-`closed` transition)
- Advancing a `transcript_text` upload's `status` off `pending` -- #18 already stores the submitted text directly on `transcript_text` at upload time, so no transcription is ever needed for this kind, but nothing in the current backlog moves its `status` to `complete` either. Same as above: a real gap, worth a follow-up when picked up, not filed here since it's outside this task's audio/video-only mandate -- #18's own Out of scope already anticipated "#19 ... can special-case `transcript_text` there if desired"; this grooming pass chooses not to, to keep this task's scope matching its title exactly
- Retrying a `failed` transcription -- calling this endpoint again on a `failed` upload is rejected with `409`, the same as `processing`/`complete`; nothing in the plan calls for a distinct retry action, so no follow-up filed
- A background job / async task queue for the transcription call -- this project has no job infrastructure (no Celery/queue), mirroring #12's identical reasoning; the call stays synchronous within this task's own endpoint request. No follow-up filed since nothing calls for one yet
- Which transcription provider to call, model/prompt choice, and API key/credential management -- treated as a config/environment concern, mirroring #12's identical Out-of-scope item; the injectable function in Constraints is the extension point a future provider gets wired in behind
- Automatically triggering transcription as part of #18's upload endpoint -- kept as a separate, explicit, facilitator-triggered action, mirroring #12's identical reasoning for keeping its AI call out of reveal: the upload endpoint must stay fast and succeed/fail purely on its own rules regardless of a transcription provider's availability or latency
- A frontend UI for triggering transcription or showing progress -- consistent with #4-#18, no template/static layer exists in the project and nothing later in the plan calls for one, so no follow-up filed
- AI extraction of decisions/action items from the resulting transcript -- that's #20's job, which depends on this task only for a `transcript_text` to exist on an `audio`/`video` upload
Constraints:
- Depends on #18 (Meeting upload page) being merged: `MeetingUpload`, `UploadKind`, `UploadStatus`, and the existing `app/uploads.py` router/response model already exist and must be reused, not re-derived. #18 is groomed but not yet implemented as of this grooming pass; if it is still unmerged when this task starts, tests needing an upload row may seed one directly via the ORM (`status=UploadStatus.PENDING`), consistent with how #18's own tests seed a closed cycle ahead of #15 landing
- Add the new route to the existing `app/uploads.py` router (from #18) rather than a new router module, following its existing Pydantic response-model / `Depends(get_db)` / `HTTPException(status_code=..., detail=...)` pattern
- Use `Depends(require_role("facilitator"))` for the route, matching #18's `POST` upload endpoint's precedent that mutating a cycle-scoped upload is facilitator-only
- The transcription call must go through a single, mockable function in a new `app/transcription.py` module (e.g. `transcribe_audio(file_path: str) -> str`), mirroring #12's `app/ai_clustering.py`/`generate_cluster_suggestions` pattern exactly: it raises `NotImplementedError` unconditionally since no provider is wired up, the endpoint catches all exceptions it raises, and tests must monkeypatch/stub it so no test makes a real network call or needs a real API key/credential in CI
- No AI/transcription SDK dependency is added to `pyproject.toml` in this task -- the interface is provider-agnostic; wiring a real provider behind it is a future concern per Out of scope
- The endpoint's checks run in this order: upload exists and belongs to the given `cycle_id`/`project_id` (`404` otherwise); `upload.kind in {UploadKind.AUDIO, UploadKind.VIDEO}` (`422` otherwise); `upload.status == UploadStatus.PENDING` (`409` otherwise). Only after all three pass does it set `status = PROCESSING`, commit, call `transcribe_audio(upload.file_path)`, then on success set `status = COMPLETE` and `transcript_text` to the returned string, or on any exception set `status = FAILED` -- committing either way and responding `200` in both outcomes. A provider failure is never surfaced as a `4xx`/`5xx`; the earlier `404`/`422`/`409` precondition checks are ordinary request-validation errors and remain as such
- No new cycle-status check is needed in this endpoint: an upload can only exist on a cycle that was `closed` at upload time (#18's own `409` gate), and nothing in the backlog reopens a closed cycle, so the cycle is always `closed` by the time an upload exists
- No new Alembic migration is needed -- `MeetingUpload.status` and `.transcript_text` already exist as columns from #18's migration
- Follow the existing FastAPI pattern in `app/cycles.py`/`app/uploads.py` for cycle/upload lookup and `404`, and `HTTPException(status_code=..., detail=...)` for errors
- Add tests under `tests/`, following the `TestClient` + in-memory-SQLite pattern already used in `tests/test_auth.py`, and #12's monkeypatch style for stubbing the injectable function

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
