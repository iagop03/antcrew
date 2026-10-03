# Run lifecycle & events

## Run states

A run moves through a fixed set of states. The platform records every transition with a timestamp.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> pending : POST /runs
    pending --> running : worker picks up
    running --> waiting_hitl : HitlReviewer fires
    waiting_hitl --> running : reviewer approves
    waiting_hitl --> failed : reviewer rejects
    running --> completed : all steps finished
    running --> failed : unhandled exception
    completed --> [*]
    failed --> [*]
```

`waiting_hitl` is the only state where the run is intentionally paused. Everything else is either in-progress or terminal.

---

## Event types

The engine writes one event per significant action via `EventBusBridge`. All events are persisted in PostgreSQL and streamed over WebSocket to any connected dashboard.

| Event type | When it fires |
|---|---|
| `pipeline.start` | Run begins execution |
| `agent.start` | A capability is dispatched |
| `agent.token` | Streaming token chunk from an LLM-backed capability — **not stored**, only live WebSocket |
| `agent.end` | Capability completes (success or error) — includes duration, cost, tokens, artifact keys |
| `pipeline.end` | Run finishes (all capabilities done or unhandled error) — includes total cost |
| `hitl.review_required` | `HitlReviewer` fires — run is now `waiting_hitl` |
| `hitl.resolved` | A human approved or rejected — run resumes or fails |

See [Event payload schema](../engine/event-schema.md) for the exact JSON shape of each event.

---

## Ticket extraction

When a run completes, the platform scans its events for any structured output matching a ticket schema. Matching objects are upserted as tickets using a workspace-scoped atomic counter:

```sql
UPDATE workspace
SET ticket_counter = ticket_counter + 1
WHERE id = :workspace_id
RETURNING ticket_prefix, ticket_counter;
-- Returns: ("PROJ", 42) → ticket display ID = "PROJ-00042"
```

Upsert means re-running the same pipeline with the same external IDs won't create duplicate tickets.

---

## Real-time streaming

The platform uses PostgreSQL LISTEN/NOTIFY to broadcast events to all connected WebSocket clients. The sequence for a live dashboard view:

```mermaid
sequenceDiagram
    participant Engine as antcrew-engine
    participant API as Platform API
    participant DB as PostgreSQL
    participant WS as WebSocket
    participant Browser

    Browser->>WS: GET /ws/runs/{run_id} — subscribe
    Engine->>API: POST /runs/{id}/events
    API->>DB: INSERT event
    DB->>API: NOTIFY channel
    API->>WS: broadcast event payload
    WS->>Browser: push update (no polling)
```

Latency from event write to browser update is typically under 100 ms.
