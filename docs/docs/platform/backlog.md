# Backlog & Sprints

The Backlog view at `/backlog` gives every workspace an autonomous ticket board: the PM agent creates and orders tickets, agents implement them, and the owner validates. No terminal required.

---

## Concept

Each `antcrew run` on a workspace produces a set of tickets. A **Sprint** is a named group of those tickets — it represents one planned development cycle. Tickets live in the backlog until you assign them to a sprint. Sprints have an order too, so the product roadmap is readable at a glance.

```
Workspace
 └─ Sprint 1 — auth module       [active]
     ├─ PROJ-00001  Design schema           [done]
     ├─ PROJ-00002  Implement endpoints     [in_progress]  depends: [PROJ-00001]
     └─ PROJ-00003  Write tests             [open]         depends: [PROJ-00002]
 └─ Sprint 2 — billing           [planning]
     └─ PROJ-00004  Stripe integration      [open]
 └─ Backlog (unassigned)
     └─ PROJ-00005  OAuth2 refresh tokens   [open]
```

---

## Sprint lifecycle

| Status | Meaning |
|---|---|
| `planning` | Tickets being assigned and ordered |
| `active` | Work in progress — the current sprint |
| `done` | Sprint completed; tickets locked |

Cycle the status with the refresh icon on each sprint header, or `PATCH /sprints/{sprint_id}`.

---

## Sprint fields

| Field | Type | Description |
|---|---|---|
| `sprint_id` | string (UUID) | Stable identifier |
| `name` | string | Human-readable sprint name |
| `status` | string | `planning` \| `active` \| `done` |
| `backlog_order` | integer | Display position in the roadmap (lower = first) |
| `default_team` | string (nullable) | Team used when dispatching tickets. Falls back to workspace `default_team`, then `FullStackTeam` |

---

## Ticket fields added for backlog

| Field | Type | Description |
|---|---|---|
| `sprint_id` | string (nullable) | Sprint this ticket belongs to; `null` = backlog |
| `backlog_order` | integer | Display position within the sprint (lower = first) |
| `depends_on` | JSON list of ticket IDs | Direct dependencies — tickets that must be done before this one |
| `implementing_run_id` | string (nullable) | ID of the run that was dispatched to implement this ticket; set automatically when the sprint runner dispatches it |

---

## API

### Sprints

```
POST   /sprints/                       Create a new sprint
GET    /sprints/                       List sprints for the workspace
GET    /sprints/{sprint_id}            Get a sprint by ID
GET    /sprints/{sprint_id}/tickets    List tickets in a sprint, sorted by backlog_order
PATCH  /sprints/{sprint_id}            Update name, status, backlog_order, or default_team
POST   /sprints/{sprint_id}/run        Dispatch the next wave of ready tickets (DAG order)
```

**Create sprint**

```json
POST /sprints/
{
  "name": "Sprint 1 — auth module",
  "status": "planning",
  "default_team": "FullStackTeam"   // optional; inherits workspace default if omitted
}
```

**Update sprint**

```json
PATCH /sprints/{sprint_id}
{ "status": "active", "default_team": null }
```

Sending `"default_team": null` clears the sprint-level override and falls back to the workspace default.

**Run a sprint**

```
POST /sprints/{sprint_id}/run
```

Returns:

```json
{
  "sprint_id": "uuid...",
  "team": "FullStackTeam",
  "dispatched": [{"ticket_id": "T-001", "run_id": "run-...", "title": "Design schema"}],
  "waiting":    ["T-002"],
  "already_done": [],
  "blocked":    []
}
```

### Ticket sprint assignment

```
PATCH /tickets/{ticket_id}/sprint
```

```json
{
  "sprint_id": "uuid...",                         // null = move to backlog
  "backlog_order": 2,                             // optional position override
  "depends_on": ["ticket-id-a", "ticket-id-b"]   // optional dependency list
}
```

---

## Default team resolution

When a sprint is dispatched, the team is resolved in order:

1. `sprint.default_team` (sprint-level override)
2. `workspace.default_team` (workspace setting, configurable in Admin → Settings)
3. `FullStackTeam` (platform default)

The resolved team is returned in the `POST /sprints/{sprint_id}/run` response.

---

## Parallel execution and DAG

Tickets within a sprint can run in parallel. The `depends_on` field on each ticket encodes direct dependencies — tickets that must reach `done` status before the dependent ticket can be dispatched.

### How it works

When `POST /sprints/{sprint_id}/run` is called:

1. Tickets with no pending dependencies are dispatched **in parallel** (wave 1).
2. Each dispatched ticket is assigned an `implementing_run_id` pointing to its run.
3. When a run completes, the platform automatically checks if any waiting tickets are now unblocked — and dispatches them without requiring another `POST /run` call (auto-wave).
4. Context flows downstream: when ticket C depends on B, the run for C receives B's output as `replay_run_id`, so the implementing agent sees what B produced.

### Dependency model

Only **direct** dependencies are listed in `depends_on`. Transitivity is implicit:

```
A → B → C
```

C lists only `[B]`. When B is done, C becomes ready. The scheduler handles the chain automatically.

### Ticket statuses during a sprint run

| Status | Meaning |
|---|---|
| `open` | Not yet dispatched |
| `in_progress` | Run dispatched, waiting for completion |
| `done` | Run completed successfully |
| `blocked` | Run completed with error; downstream deps will not be dispatched |

### How the PM agent assigns dependencies

When a PM run produces a batch of tickets, the `dependencies` field on each ticket is parsed automatically. Only IDs from the **same batch** are accepted as valid dependencies (cross-batch IDs are ignored), and self-references are dropped. This prevents stale pointers and keeps the dependency graph local to the sprint.

---

## Workflow

1. Run a pipeline — the PM agent creates tickets and populates `depends_on` automatically.
2. Open `/backlog` — new tickets appear in the **Backlog** section (unassigned).
3. Create a sprint → add tickets from the backlog → set order with ↑/↓ buttons.
4. Optionally adjust `depends_on` per ticket with `PATCH /tickets/{id}/sprint`.
5. Click **Run** on the sprint header — the first wave dispatches immediately.
6. Subsequent waves fire automatically as each run completes (auto-wave).
7. When all tickets are `done`, mark the sprint `done` and plan the next one.

---

## Autonomous ticket creation

When an agent run completes, the PM agent creates tickets and assigns them to the workspace. Tickets arrive in the backlog with no sprint assigned. The owner reviews, assigns them to a sprint, and validates the order. From that point, clicking **Run** on the sprint dispatches all ready tickets and the auto-wave takes over.

This gives the owner a clear view of the project's state and its evolution — without manual ticket entry.
