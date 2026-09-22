# Weekly Project Feedback Tool --- Consolidated Scope

## 1. Product Idea

Build a **project-centric weekly feedback tool** that combines:

1.  **Project Weekly Pulse** --- understand how the project is
    progressing.
2.  **Team Retrospective** --- collect and discuss team feedback using
    Start / Stop / Continue.

The application itself can be developed using **AI development tools**.
AI inside the final product is a separate capability and should remain
lightweight in V1.

The guiding principle is to **keep V1 simple and add sophisticated
capabilities later**.

------------------------------------------------------------------------

## 2. Central Object: Project

The **Project** is the central object.

A project contains:

-   Project name and description
-   Members
-   Configurable roles and permissions
-   Start and end dates
-   Milestones
-   Weekly feedback cycles
-   Historical weekly summaries and actions

Roles should be configurable rather than hard-coded.

Possible roles include Project Manager, Developer, Analyst, Sponsor,
Facilitator, Reviewer, or other project-specific roles.

------------------------------------------------------------------------

## 3. Weekly Feedback Cycle

A weekly cycle combines two related concepts.

### 3.1 Project Pulse

The Project Pulse captures a short view of overall project health.

Possible fields include:

-   Overall status
-   Progress
-   Milestones and deadlines
-   Blockers
-   Risks
-   Next steps
-   Optional comments

For V1, overall status can use:

-   **On Track**
-   **At Risk**
-   **Off Track**

The feedback is about the **project**, not individual employee
performance.

The preferred input format is a **short structured form with optional
free-text comments**.

Questions may contain:

-   Common core questions for everyone
-   Additional questions based on project role

------------------------------------------------------------------------

## 4. Team Retrospective

The retrospective requirements use the course specification as the
baseline.

### Who contributes feedback?

**All team members.**

### What format do they use?

**Start / Stop / Continue.**

Participants create feedback cards under these three categories.

### Is feedback anonymous?

Names appear by default, but contributors can choose to remain
**anonymous**.

### What can participants see before the reveal?

Participants can see **only their own cards**.

### How is feedback revealed?

A **facilitator reveals all cards at the same time**.

### What happens after the reveal?

The team:

1.  Reviews the revealed cards.
2.  Clusters related cards/topics.
3.  Votes on the topics to discuss.

### How does voting work?

Each participant receives **three votes**.

A participant may assign multiple votes to the same topic.

### What does the team record?

The team records:

-   Decisions
-   Action items

These become part of the weekly project history.

------------------------------------------------------------------------

## 5. Meeting Recording / Supporting Material

The facilitator can upload supporting material after the meeting,
including:

-   Audio
-   Video
-   Transcript

**Built-in meeting recording is explicitly outside V1.**

------------------------------------------------------------------------

## 6. AI in V1

AI inside the product should remain **lightweight**.

The primary V1 AI capability is:

> Generate a short consolidated summary from the collected weekly
> project feedback.

A designated project role reviews the AI-generated summary before it
becomes final.

The reviewer can:

1.  Review the submitted feedback.
2.  Review the generated summary.
3.  Edit the summary.
4.  Approve the final weekly project summary.

AI should therefore **assist**, not automatically publish the official
project report.

------------------------------------------------------------------------

## 7. Potential Future AI Capabilities

Later versions could use AI to:

-   Detect disagreements between participants.
-   Identify recurring blockers and risks across weeks.
-   Identify project trends.
-   Suggest project status.
-   Assist with clustering retrospective cards.
-   Summarize retrospective discussions.
-   Extract decisions and action items from uploaded transcripts.
-   Generate management-level reports.

These are future capabilities, not V1 requirements.

------------------------------------------------------------------------

## 8. Conceptual Weekly Flow

``` text
PROJECT
   |
   +--> WEEKLY CYCLE
           |
           +--> PROJECT PULSE
           |       |
           |       +-- Status
           |       +-- Progress
           |       +-- Milestones
           |       +-- Risks / Blockers
           |       +-- Next Steps
           |
           +--> TEAM RETROSPECTIVE
                   |
                   +-- Start
                   +-- Stop
                   +-- Continue
                          |
                     Cards Hidden
                          |
                     Facilitator Reveal
                          |
                       Cluster
                          |
                        Vote
                          |
                      Discuss
                          |
                Decisions + Actions
```

The exact relationship/order between the Project Pulse and retrospective
remains open for refinement.

------------------------------------------------------------------------

## 9. V1 Project Setup

For V1, project setup should include:

-   Project name
-   Project description
-   Members
-   Configurable roles and permissions
-   Start date
-   End date
-   Milestones

Project documents and deeper integrations can be added later.

------------------------------------------------------------------------

## 10. Human Control

Human review is an important design principle.

The system should not automatically turn AI output into an official
project report.

A designated role should **review, edit and approve** the consolidated
weekly summary.

------------------------------------------------------------------------

## 11. V1 Boundaries

To avoid over-engineering, V1 should not initially include:

-   Built-in audio/video recording
-   Advanced AI analysis
-   Complex workflow automation
-   Sophisticated notification mechanisms
-   External project-management integrations
-   Advanced document management
-   Automated risk prediction
-   Complex cross-project analytics

------------------------------------------------------------------------

## 12. Open Design Questions

The following points have not yet been finalized:

-   Whether Project Pulse always precedes the retrospective or whether
    they are independent modules.
-   Exact weekly cycle scheduling/trigger.
-   Detailed role and permission matrix.
-   Exact Project Pulse questions.
-   Exact role-specific questions.
-   Notification mechanism.
-   Management/cross-project dashboard.
-   Technology stack.
-   AI model/provider.
-   AI development tool(s) used to build the application.

------------------------------------------------------------------------

## 13. Current Product Definition

> A project-centric weekly collaboration tool combining a **Project
> Pulse** with a **Start / Stop / Continue team retrospective**. Team
> members provide project feedback, retrospective cards remain private
> until a facilitator reveals them, related feedback is clustered and
> voted on, and the team records decisions and action items. Lightweight
> AI assists by summarizing collected feedback, while a designated human
> reviewer edits and approves the final weekly project summary.

------------------------------------------------------------------------

## 14. Suggested Next Step

Before coding, convert this scope into:

1.  **V1 feature list**
2.  **Screens and user journey**
3.  **Roles and permissions**
4.  **Simple data model**

Then select the **AI development tool and implementation stack** and
build the first working version.
