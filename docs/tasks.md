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
