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
Goal: Support project-level roles instead of hard-coded ones.
Description: Add a `ProjectMembership` entity linking a user (task 3) to a project (task 2) with a role. Add a reusable permission-check mechanism (a FastAPI dependency) that any route can use to require a role. For the MVP, only seed the two roles from the plan — Team Member and Facilitator — but store the role as data on the membership row, not as hard-coded branching in application code, so a future custom role doesn't require a code change.
Depends on: #2 (Core data model), #3 (User authentication)
Out of scope: any UI or endpoint for creating or editing custom roles — the plan only calls for the two MVP roles; this task is about the schema and permission-check mechanism being role-name-agnostic, not about exposing role management to users.
Acceptance Criteria:
- A `ProjectMembership` (or equivalent) row links a user, a project, and a role
- Team Member and Facilitator exist as seeded data, not Python constants compared with if/elif
- A reusable permission-check dependency exists (e.g. `require_role("facilitator")`) that can be applied to any route
- Since no protected route exists yet, add one minimal example route guarded by the dependency to prove it end-to-end
- A test shows that route returns 403 for a member without the required role and 200 for one with it

## 5. Create and view a project
Goal: Let a user create a project and see its page.
Description: Build the flow to create a project (name, description, start/end dates) and a project page that currently shows just this static info. Creating a project also makes the creator a member of it with the Facilitator role — the plan treats the creator as the usual facilitator, and without this no one could ever pass #6's "only a facilitator can create a cycle" check. This becomes the landing page other features will add sections to later.
Depends on: #2 (Core data model), #3 (User authentication), #4 (Configurable roles and permissions)
Acceptance Criteria:
- A logged-in user can create a project with name, description, start date, and end date
- Creating a project also creates a `ProjectMembership` for the creator with the Facilitator role
- The created project is viewable on its own project page
- Submitting without a required field (e.g. name) is rejected with a clear error

## 6. Create a feedback cycle and invite the team
Goal: Let a facilitator start a weekly feedback cycle and add members to the project.
Description: On a project, allow a facilitator to open a new feedback cycle, and add other users to the project as Team Members (creating a `ProjectMembership` per #4 for anyone not already a member). Membership lives on the project, not the cycle, so it carries over automatically to future cycles. The cycle should have an open/closed state that later tasks can check.
Depends on: #2 (Core data model), #4 (Configurable roles and permissions), #5 (Create and view a project)
Acceptance Criteria:
- A facilitator can open a new feedback cycle on a project
- The facilitator can add users to the project as Team Members
- The cycle exposes an open/closed state
- A non-facilitator cannot create a cycle

## 7. Feedback submission form (Start / Stop / Continue)
Goal: Let a team member submit feedback cards for an open cycle.
Description: Build a form with three sections — Start, Stop, Continue — where a member can add multiple short cards. Submitted cards are saved against the current cycle and the submitting user — this is where `Card` gets its `author` link, deferred by #2 until a `User` existed.
Depends on: #2 (Core data model), #3 (User authentication), #6 (Create a feedback cycle and invite the team)
Acceptance Criteria:
- A member can add multiple cards under each of Start, Stop, and Continue
- Submitted cards are persisted with a link to the cycle and the author
- Submitting to a closed cycle is rejected
- Only a member of the project can submit cards to one of its cycles

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
Goal: Let the facilitator move through the vote-ranked topics during the meeting.
Description: Build a discussion view listing topics in vote order, where the facilitator can mark each one Discussed, Skipped, or Deferred. This is a live, in-meeting control surface, not a historical report. "Topic" here is the same `Cluster` entity from #2/#11 — reveal it in vote order rather than modeling anything new.
Depends on: #4 (Configurable roles and permissions), #14 (Reveal vote results after voting closes)
Acceptance Criteria:
- Topics are listed in vote-ranked order
- The facilitator can set a topic's status to Discussed, Skipped, or Deferred
- A non-facilitator cannot change topic status
- Status changes are reflected immediately to other viewers

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
