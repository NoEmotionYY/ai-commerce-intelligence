# Repository Instructions

## Mission

Build the complete AI Commerce Operations & Marketing Intelligence Agent.

Always read before planning or implementation:

1. docs/PROJECT_SPEC.md
2. docs/ACCEPTANCE.md
3. docs/ARCHITECTURE.md if present
4. docs/TASKS.md if present
5. docs/PROGRESS.md if present
6. docs/DECISIONS.md if present

PROJECT_SPEC defines WHAT the product must achieve.

ACCEPTANCE defines WHEN the project is complete.

Do not blindly copy implementation suggestions from PROJECT_SPEC if a safer,
simpler, more testable architecture satisfies the same requirement.


## Autonomous Workflow

Do not stop after planning.

Do not stop after scaffolding.

For each task:

inspect
→ implement
→ test
→ diagnose failures
→ fix
→ retest
→ review
→ document
→ continue

Continue to the next incomplete task unless there is a genuine external blocker.


## Project State

Maintain:

- docs/ARCHITECTURE.md
- docs/TASKS.md
- docs/PROGRESS.md
- docs/DECISIONS.md

Update TASKS and PROGRESS after meaningful milestones.

Important architecture decisions must be recorded in DECISIONS.md.


## Architecture

Keep these layers separated:

- API
- Agent orchestration
- business logic
- ERP integration
- crawler
- database/persistence
- UI

LLMs must never directly perform production-style database writes.

Write operations must use:

Agent
→ validated Tool
→ business service/API
→ persistence


## Deterministic Logic

Use Python rather than the LLM for deterministic calculations including:

- revenue
- profit
- margin
- ROI
- ROAS
- refund rate
- inventory days
- anomaly thresholds

Use the LLM for:

- intent understanding
- tool selection
- orchestration
- explanation
- qualitative analysis


## Agent Design

Prefer:

single Agent + Tools + LangGraph

unless real requirements justify multi-agent architecture.

Use LangGraph for stateful workflows and Human-in-the-loop.


## High-Risk Operations

Read operations may be automatic.

High-impact write operations require approval.

A purchase order must never execute before approval.


## Crawler

Support both:

- HTTPX
- Playwright

Prefer HTTPX when browser rendering is unnecessary.

Crawler must include:

- retry
- timeout
- pagination where applicable
- rate limiting
- validation
- deduplication
- persistence
- task state
- error logging

Maintain a deterministic local mock competitor website for demo reliability.

Do not implement access-control or CAPTCHA bypass.


## Testing

Every important business behavior must have meaningful tests.

Do not:

- delete tests to hide bugs
- weaken valid assertions merely to pass
- hardcode demo answers
- silently skip mandatory tests


## Subagents

Use subagents primarily for bounded read/review tasks such as:

- repository exploration
- architecture review
- test review
- security review
- crawler review
- final acceptance audit

Avoid multiple agents concurrently modifying overlapping files.

The main agent owns integration.


## Git

Keep the repository recoverable.

Prefer coherent checkpoints before major architectural changes.

Do not rewrite history without explicit need.


## Final Verification

Before declaring completion:

1. reread PROJECT_SPEC
2. reread ACCEPTANCE
3. audit implementation against both
4. search mandatory TODO/FIXME/placeholders
5. run full tests
6. run lint/type checks
7. build Docker from scratch
8. launch Docker Compose
9. verify health checks
10. initialize clean database
11. run seed process
12. run ERP E2E
13. run crawler E2E
14. run Agent E2E
15. run purchase approval E2E
16. run A102 combined-analysis scenario
17. delegate an independent final review
18. fix Critical and High findings
19. rerun affected tests
20. rerun the final suite

Create docs/FINAL_REPORT.md only after these checks.

Never claim a check passed unless it was actually executed.