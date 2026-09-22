# Backlog

## 1. Project scaffold with a passing test
Goal: Have an empty, runnable project with a working test harness.
Description: Initialize the repository structure for the chosen stack (frontend/backend/build tooling as applicable), wire up a test runner, and add one trivial test that passes. This confirms the toolchain works before any real feature is built.

## 2. Core data model for projects, cycles, and cards
Goal: Define and persist the core entities the product is built on.
Description: Create the data model (and migrations, if applicable) for Project, Feedback Cycle, Feedback Card, Cluster, Vote, and Action Item, including how they relate to each other. Include the fields called out in the plan (e.g. card category, anonymity flag, action status) even if most business logic isn't built yet.

## 3. User authentication
Goal: Let a person sign up and log in to the app.
Description: Implement account creation and login (email/password or a chosen provider), with sessions or tokens protecting authenticated routes. No roles or permissions logic yet — just "who is this user."

## 4. Configurable roles and permissions
Goal: Support project-level roles instead of hard-coded ones.
Description: Add a roles concept scoped to a project (e.g. Team Member, Facilitator, plus room for custom roles) and a permission check mechanism that other features can call. Seed the two MVP roles described in the plan: Team Member and Facilitator.

## 5. Create and view a project
Goal: Let a user create a project and see its page.
Description: Build the flow to create a project (name, description, start/end dates) and a project page that currently shows just this static info. This becomes the landing page other features will add sections to later.

## 6. Create a feedback cycle and invite the team
Goal: Let a facilitator start a weekly feedback cycle and add members.
Description: On a project, allow a facilitator to open a new feedback cycle and invite team members to it (by adding existing users or sending an invite). The cycle should have an open/closed state that later tasks can check.

## 7. Feedback submission form (Start / Stop / Continue)
Goal: Let a team member submit feedback cards for an open cycle.
Description: Build a form with three sections — Start, Stop, Continue — where a member can add multiple short cards. Submitted cards are saved against the current cycle and the submitting user.

## 8. Anonymous submission handling
Goal: Let contributors mark individual cards as anonymous, and enforce it everywhere.
Description: Add an anonymous checkbox per card, and make sure the author is hidden from every view and API response for anonymous cards — including to the facilitator. Write a test that specifically checks a facilitator-facing view cannot see the anonymous author.

## 9. Private view of own feedback before reveal
Goal: Let a member see and edit only their own cards before the reveal.
Description: Build the pre-reveal view of the feedback form/list that shows a user their own submitted cards (editable) and gives no visibility into anyone else's submissions. Enforce this on the backend, not just by hiding UI elements.

## 10. Facilitator reveal action
Goal: Let the facilitator make all submitted cards visible to the team at once.
Description: Add a "reveal" action, available only to the facilitator, that flips the cycle into a revealed state and makes every submitted card (respecting anonymity) visible to all team members in a single view.

## 11. Manual clustering board
Goal: Let the team organize revealed cards into clusters by hand.
Description: Build a board view of revealed cards where users can move cards between clusters, merge or split clusters, rename clusters, and leave cards ungrouped. Persist cluster membership so it survives a page reload.

## 12. Automatic clustering suggestions
Goal: Pre-group revealed cards into suggested clusters using AI.
Description: When a cycle is revealed, call an AI service to propose an initial clustering of the cards and populate the clustering board with these suggestions. The output must be fully editable using the manual clustering controls — this task only adds the suggestion step, not any locking behavior.

## 13. Voting on discussion topics
Goal: Let each team member distribute 3 votes across clusters.
Description: Add a voting UI where each participant gets exactly 3 votes to allocate across clusters, including the ability to put more than one vote on the same cluster. Store each vote so results can be tallied later.

## 14. Reveal vote results after voting closes
Goal: Show vote totals only once voting is complete.
Description: Hide individual and running vote totals while voting is open, and reveal the final tally (ranked by votes) once every participant has voted or the facilitator manually closes voting. This produces the prioritized discussion agenda referenced elsewhere in the plan.

## 15. Discussion stage with topic status
Goal: Let the facilitator move through the vote-ranked topics during the meeting.
Description: Build a discussion view listing topics in vote order, where the facilitator can mark each one Discussed, Skipped, or Deferred. This is a live, in-meeting control surface, not a historical report.

## 16. Record notes, decisions, and action items during discussion
Goal: Let the team capture outcomes while discussing a topic.
Description: On the discussion view, let any team member add free-text notes and structured decision/action-item entries tied to the topic currently being discussed. This is manual entry during the live meeting, separate from the later AI-assisted extraction from recordings.

## 17. Action item tracking and status updates
Goal: Give action items a life beyond the meeting they were created in.
Description: Build a view listing all action items for a project with their description, owner, optional due date, status (Open/Done), and related topic. Let the assigned owner mark their own action items as Done.

## 18. Meeting upload page
Goal: Let the facilitator attach a record of the meeting after it happens.
Description: Build an upload page where the facilitator can attach audio, video, a transcript file, or pasted transcript text to a completed cycle, and see the upload's processing status. This task covers the upload and status UI only, not the processing itself.

## 19. Transcript generation from uploaded audio/video
Goal: Turn an uploaded audio or video file into text.
Description: Add a background job that takes an uploaded audio/video file, runs it through a transcription service, and stores the resulting transcript text against the cycle. Update the processing status so the upload page (task 18) can reflect progress and completion.

## 20. AI extraction of decisions and action items
Goal: Turn a transcript into draft decisions and action items.
Description: Add a background job that sends a stored transcript to an AI service and produces a draft list of decisions, action items (with owner and due date when mentioned), and a short summary. Save these as unconfirmed drafts — nothing here is shown to the team as final yet.

## 21. Facilitator review and confirmation of extracted items
Goal: Let the facilitator approve, edit, or discard AI-suggested outcomes.
Description: Build a review screen showing the AI-drafted decisions, action items, and summary from task 20, where the facilitator can edit any field and confirm each item before it becomes part of the permanent record. Nothing from the extraction job should reach the team-facing summary without going through this step.

## 22. Retrospective summary page
Goal: Give the team a single page summarizing a completed cycle.
Description: Build a summary page showing the top discussion topics, key notes, confirmed decisions, confirmed action items, attendance/participation, and the original feedback cards for a closed cycle. This is a read-only page assembled from data produced by earlier tasks.

## 23. Project dashboard page
Goal: Give a project a home page summarizing its ongoing state.
Description: Build the main project page showing the current feedback cycle and submission status, any active retrospective, links to previous retrospectives, and a list of open action items. This ties together the project-level views built in other tasks into one landing page.
