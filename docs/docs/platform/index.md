# Platform

The antcrew platform is the managed cloud service your team uses to observe, store, and gate AI agent workflows. It does not execute agent code — it receives runs from the engine, stores their full history, and surfaces review queues to your team.

Access it at **[antcrew.org](https://antcrew.org)**.

---

## What it manages

**Runs** — every agent execution started via the engine is a run. The platform stores its full history: status transitions, TraceLog events, generated tickets, and any HITL reviews it triggered.

**Tickets** — structured action items extracted from run output. Each workspace has a configurable prefix (`PROJ`, `ENG`, `BUG`…). Tickets get sequential display IDs — `PROJ-00001`, `PROJ-00002` — and link back to the run that created them.

**HITL reviews** — when the engine reaches a HITL checkpoint (via the `HitlReviewer` capability), it sends a review request to the platform. The platform queues it, notifies the right reviewer, and holds the `resume` signal until they approve or reject.

**Workspaces** — isolated tenants. Each workspace has its own API keys, members, ticket prefix, provider keys, and run history. Nothing leaks between workspaces.

**Evals** — automated quality checks that run against past runs on a schedule or on demand.

**GitHub App** — connects repositories to workspaces for write-back (open PRs from runs) and push triggers (auto-dispatch a run on every push).

**Documentation S3** — connect an S3 bucket to a workspace so agents automatically receive relevant docs context on every run. Upload files, set a schema YAML that maps doc types to agents, and trigger a re-index. Supports markdown, PDF, Word, and COBOL source files.

**Webhooks** — outbound notifications to your own endpoints when runs complete, tickets are created, or reviews are resolved.

---

## Real-time dashboard

The dashboard subscribes to live events and updates without polling. You see:

- Current run status and elapsed time
- TraceLog events as they stream in — each LLM call, tool invocation, intermediate result
- Tickets as they are created
- HITL review cards with the full context the agent passed in

---

## Data model

```mermaid
erDiagram
    Workspace ||--o{ Run : "contains"
    Workspace ||--o{ ApiKey : "has"
    Run ||--o{ Event : "logs"
    Run ||--o{ Ticket : "generates"
    Run ||--o{ Review : "gates on"
    Review }o--|| Ticket : "may reference"
```

---

## Key concepts

| Concept | Description |
|---|---|
| **Workspace** | Isolated tenant — own keys, members, tickets, runs |
| **Run** | One agent pipeline execution — has status, events, tickets |
| **Event** | A single TraceLog entry — LLM call, tool result, state change |
| **Ticket** | Structured action item from a run, with a display ID |
| **Sprint** | Named group of tickets representing a development cycle |
| **Backlog** | Unassigned tickets waiting to be placed in a sprint |
| **HITL Review** | Human approval gate that pauses a run until resolved |
| **Eval** | Automated quality check over run output |
| **Webhook** | Outbound HTTP call to your system on any platform event |

[:octicons-arrow-right-24: Self-hosted quickstart — single docker run](self-hosted.md)

[:octicons-arrow-right-24: Quick start — connect your first pipeline](getting-started.md)

[:octicons-arrow-right-24: Workspaces & API keys](workspaces.md)

[:octicons-arrow-right-24: Runs & tickets](runs.md)

[:octicons-arrow-right-24: Backlog & sprints](backlog.md)

[:octicons-arrow-right-24: HITL reviews](hitl.md)

[:octicons-arrow-right-24: GitHub App integration](github-app.md)

[:octicons-arrow-right-24: Documentation S3 integration](docs-s3.md)
