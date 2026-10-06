# Engine event payload schema

When a run executes through antcrew-platform, the engine emits events that are forwarded
to the platform's event bus via `EventBusBridge` and stored in the `event` table
(`event.event_type`, `event.payload`). This page documents the exact JSON schema of each
payload so you can query them directly or build analytics on top.

---

## How to access events

**Via the platform API:**
```
GET /runs/{run_id}/events
```
Returns a list of `{event_type, payload, timestamp, recorded_at}` objects in emission order.

**Via SQL (Neon, direct):**
```sql
SELECT event_type, payload, recorded_at
FROM event
WHERE run_id = 'run_01abc...'
ORDER BY timestamp;
```

Events are retained for `DATA_RETENTION_DAYS` (default: 30) days. `Run` metadata rows
are not deleted.

---

## Event types and payload schemas

### `pipeline.start`

Emitted by the platform when a run begins execution.

```json
{
  "run_id":    "run_01abc...",
  "thread_id": "default",
  "team":      "dev"
}
```

---

### `agent.start`

Emitted when the engine dispatches a capability.

```json
{
  "agent_name": "CodeGenerator",
  "run_id":     "run_01abc...",
  "thread_id":  "default"
}
```

---

### `agent.token`

Emitted for each streaming token chunk from an LLM-backed capability.
**Not stored** in the `event` table (fire-and-forget to avoid unbounded growth) —
only delivered to live WebSocket subscribers.

```json
{
  "agent_name": "CodeGenerator",
  "chunk":      "def authenticate(",
  "run_id":     "run_01abc...",
  "thread_id":  "default"
}
```

---

### `agent.end`

Emitted when a capability completes (success or error).

```json
{
  "agent_name":         "CodeGenerator",
  "duration_s":         3.142,
  "duration_ms":        3142,
  "cost_usd":           0.000842,
  "tokens_in":          1204,
  "tokens_out":         387,
  "cache_read_tokens":  0,
  "cache_write_tokens": 0,
  "produced_keys":      ["source/auth.py"],
  "succeeded":          true,
  "errors":             [],
  "run_id":             "run_01abc...",
  "thread_id":          "default"
}
```

| Field | Type | Notes |
|---|---|---|
| `agent_name` | string | Matches `CapabilityDescriptor.name` |
| `duration_s` | float | Execution time in seconds (3 decimal places) |
| `duration_ms` | int | Same, in milliseconds — use this for `AVG()` queries |
| `cost_usd` | float | LLM cost attributed to this capability run |
| `tokens_in` | int | Input/prompt tokens. `0` if the capability did not set this field |
| `tokens_out` | int | Output/completion tokens. `0` if not set |
| `cache_read_tokens` | int | Tokens served from Anthropic prompt cache (0 for other providers) |
| `cache_write_tokens` | int | Tokens written to Anthropic prompt cache (0 for other providers) |
| `produced_keys` | string[] | Artifact IDs created by this capability in this run |
| `succeeded` | bool | `true` when `errors` is empty |
| `errors` | string[] | Error messages if the capability failed |

!!! note "tokens_in / tokens_out availability"
    These fields are populated by capabilities that call an LLM and explicitly set them
    on the `CapabilityResult`. Until a specific capability is updated to set these fields,
    they will be `0`. `cache_read_tokens` and `cache_write_tokens` are populated by all
    Anthropic-backed capabilities via the prompt cache usage headers.

---

### `pipeline.end`

Emitted by the platform when a run finishes (all capabilities done or error).

```json
{
  "run_id":       "run_01abc...",
  "thread_id":    "default",
  "status":       "completed",
  "cost_usd":     0.00312,
  "duration_s":   18.4
}
```

---

### `hitl.review_required`

Emitted by `PlatformChannel.send_for_review()` **before** the run blocks waiting for a decision. This is the trigger signal for external reviewers and integrations.

```json
{
  "review_id":            "rev_01xyz...",
  "agent_name":           "BusinessAnalystAgent",
  "options":              ["approve", "edit", "reject"],
  "artifact":             { "...": "serialized artifact" },
  "review_type":          "approval",
  "hitl_channel":         "slack",
  "feedback_schema_json": "{\"type\":\"object\",\"properties\":{...}}",
  "run_id":               "run_01abc...",
  "thread_id":            "default"
}
```

| Field | Type | Notes |
|---|---|---|
| `review_id` | string | UUID — use this to resolve the review via `POST /reviews/{id}` |
| `agent_name` | string | Agent that triggered the review |
| `options` | string[] | Allowed decisions (default: `["approve","edit","reject"]`) |
| `artifact` | object or array | Serialized agent output awaiting review |
| `review_type` | string | `"approval"` or `"structured_list"` |
| `hitl_channel` | string | Routing hint: `"default"`, `"slack"`, or custom — matches `Agent.hitl_channel` |
| `feedback_schema_json` | string or null | JSON Schema for structured feedback; null if agent has no `feedback_schema` |

---

### `hitl.resolved`

Emitted when a HITL review is approved or rejected by a human reviewer.

```json
{
  "review_id": "rev_01xyz...",
  "verdict":   "approved",
  "run_id":    "run_01abc...",
  "thread_id": "default"
}
```

---

## Example analytics queries

All queries run directly against the `event` table on Neon (or equivalent PostgreSQL).

**Which capabilities are used most?**
```sql
SELECT payload->>'agent_name' AS capability, COUNT(*) AS runs
FROM event
WHERE event_type = 'agent.end'
GROUP BY 1
ORDER BY 2 DESC;
```

**Average duration per capability (ms):**
```sql
SELECT
  payload->>'agent_name'         AS capability,
  AVG((payload->>'duration_ms')::int) AS avg_duration_ms,
  COUNT(*)                            AS runs
FROM event
WHERE event_type = 'agent.end'
GROUP BY 1
ORDER BY 2 DESC;
```

**Error rate per capability:**
```sql
SELECT
  payload->>'agent_name'                                        AS capability,
  COUNT(*) FILTER (WHERE (payload->>'succeeded')::bool = false) AS errors,
  COUNT(*)                                                       AS total,
  ROUND(
    100.0 * COUNT(*) FILTER (WHERE (payload->>'succeeded')::bool = false) / COUNT(*), 1
  ) AS error_pct
FROM event
WHERE event_type = 'agent.end'
GROUP BY 1
ORDER BY 3 DESC;
```

**Average HITL resolution time (minutes):**
```sql
SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 60) AS avg_resolution_min
FROM hitl_review
WHERE resolved_at IS NOT NULL;
```

**Total cost and tokens by run:**
```sql
SELECT
  run_id,
  SUM((payload->>'cost_usd')::float)    AS total_cost_usd,
  SUM((payload->>'tokens_in')::int)     AS total_tokens_in,
  SUM((payload->>'tokens_out')::int)    AS total_tokens_out
FROM event
WHERE event_type = 'agent.end'
GROUP BY run_id
ORDER BY total_cost_usd DESC
LIMIT 20;
```

---

## `agent_event` table

Each `agent.end` event is also persisted as a row in the `agent_event` table for fast aggregation without JSON parsing. Prefer this table for cost and performance queries over the raw `event` table.

| Column | Type | Notes |
|---|---|---|
| `run_id` | string | References `run.run_id` |
| `agent_name` | string | Agent class name |
| `duration_s` | float | Execution time in seconds |
| `tokens_in` | int | Input tokens |
| `tokens_out` | int | Output tokens |
| `cost_usd` | float | Cost attributed to this agent invocation |
| `produced_keys` | string | JSON-encoded `list[str]` of artifact keys produced |
| `recorded_at` | datetime | UTC timestamp when the row was inserted |

Access via the API: `GET /runs/{run_id}/agents`

```sql
-- Cost breakdown by agent across all runs
SELECT agent_name, SUM(cost_usd) AS total_cost, SUM(tokens_in + tokens_out) AS total_tokens
FROM agent_event
GROUP BY agent_name
ORDER BY total_cost DESC;
```
