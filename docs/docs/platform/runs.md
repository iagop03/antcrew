# Runs & tickets

## Runs

A **run** is one execution of an agent pipeline. Runs are async — the API accepts the request immediately (HTTP 202) and returns a `run_id`; the pipeline executes in the background.

### Start a run

```bash
curl -X POST https://antcrew.org/run/ \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{"team": "DevTeam", "request": "Build a JWT authentication module"}'
```

```json
{
  "status": "accepted",
  "run_id": "abc123def456",
  "team": "DevTeam",
  "hint": "Poll GET /runs or connect to WS /ws/events for real-time updates"
}
```

### Available teams

| Team | Agents | Best for |
|---|---|---|
| `DevTeam` | BA → PM → BackendDev | Feature tickets + backend implementation |
| `FullStackTeam` | Scanner → BA → PM → Sprint → Backend → Frontend → QA → Reviewer → DevOps → DocWriter | Full sprint cycle |
| `ResearchTeam` | Researcher → Copywriter | Research reports |
| `ContentTeam` | Idea → Copywriter → Editor | Blog posts, content |
| `FeatureTeam` | Feature | End-to-end feature implementation |

List available teams at runtime: `GET /run/teams`

List agents in a team: `GET /run/teams/{team}/agents`

### Run parameters

| Field | Type | Description |
|---|---|---|
| `team` | string | Team name (required) |
| `request` | string | Task description (required) |
| `thread_id` | string | Groups runs into a conversation thread (default `"default"`) |
| `max_cost_usd` | float | Hard budget cap — run stops if exceeded |
| `hitl` | bool | Pause at each checkpoint for human review |
| `repo_url` | string | Git repo to clone and inject as context |
| `repo_token` | string | PAT for private HTTPS repos (never stored) |
| `model` | string | Run-level model override (e.g. `"groq:llama-3.3-70b-versatile"`) |
| `model_overrides` | object | Per-agent model overrides — see [Model configuration](model-config.md) |
| `client_label` | string | Cost-center / client tag for spend breakdown |
| `write_back` | bool | Push generated artifacts to repo as a PR after run |
| `dry_run` | bool | Suppress write-back and sandbox side effects; LLMs still run normally |
| `org_context` | object | Pre-populate `ProjectKB` before the run — keys: `decisions`, `tech_stack`, `dependencies` |
| `replay_run_id` | string | Inject artifacts from a past run as context for BA and PM agents (requires ChromaMemory) |

### Run statuses

| Status | Meaning |
|---|---|
| `running` | Actively executing |
| `success` | Finished successfully |
| `error` | Ended with an error |
| `cancelled` | Cancelled by user |

### Poll and stream

```bash
# Get run details
GET /runs/{run_id}

# Get all events (stored)
GET /runs/{run_id}/events

# Per-agent cost and token breakdown
GET /runs/{run_id}/agents

# ComparisonLLM results (only available when multi-model comparison was used)
GET /runs/{run_id}/comparison

# Stream events live (WebSocket — all runs)
wss://antcrew.org/ws/events

# Download final artifacts
GET /runs/{run_id}/artifacts.zip
```

#### `GET /runs/{run_id}/agents` response

Returns one entry per agent invocation captured from the `agent.end` event. Returns an empty list while the run is still in progress.

```json
{
  "run_id": "abc123",
  "agents": [
    {
      "agent_name": "BusinessAnalystAgent",
      "duration_s": 4.21,
      "tokens_in": 1842,
      "tokens_out": 512,
      "cost_usd": 0.000641,
      "produced_keys": ["prd"],
      "recorded_at": "2026-08-13T10:00:01Z"
    }
  ]
}
```

#### `GET /runs/{run_id}/comparison` response

Only available when the run used a `ComparisonLLM` (multi-model comparison). Returns 404 with `"No comparison data"` otherwise.

```json
{
  "run_id": "abc123",
  "comparison_log": [
    {
      "model": "claude:claude-sonnet-5",
      "output": "...",
      "latency_s": 3.1,
      "cost_usd": 0.0012
    }
  ]
}
```

### Re-run

In the dashboard, open a completed run and click **Re-run** in the sidebar to resubmit the same team and request. Via API, post to `/run/` again with the same body.

---

## Model configuration

Override which LLM each agent uses at run level or workspace level. See [Model configuration](model-config.md) for the full reference including presets.

**Quick example — mix models in one run:**

```json
{
  "team": "FullStackTeam",
  "request": "Add OAuth2 login",
  "model_overrides": {
    "default": "groq:llama-3.3-70b-versatile",
    "BackendDevAgent": "claude:claude-sonnet-5",
    "ReviewerAgent": "claude:claude-opus-5"
  }
}
```

---

## Tickets

Tickets are structured action items extracted from run output by the `PMAgent`.

### List tickets

```bash
GET /tickets/?run_id={run_id}
GET /tickets/               # all tickets for the workspace
```

### Display IDs

Each workspace has a configurable prefix (e.g. `PROJ`). Tickets get sequential IDs: `PROJ-00001`, `PROJ-00002`, etc. Configure the prefix in **Settings → Ticket settings**.

### GitHub linking

If a workspace has a GitHub repo configured, the ticket detail view shows linked commits and PRs that include the ticket display ID in their commit message.

---

## Real-time event stream (SSE)

`GET /runs/{run_id}/stream` streams run events as [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events) while a run is active, and replays missed events automatically on reconnect.

### Connecting

=== "curl"

    ```bash
    curl -N \
      -H "X-Api-Key: acw_..." \
      "https://antcrew.org/runs/abc123/stream"
    ```

=== "JavaScript"

    ```js
    const es = new EventSource(
      "https://antcrew.org/runs/abc123/stream",
      { headers: { "X-Api-Key": "acw_..." } }
    );

    es.addEventListener("agent.start", e => {
      const data = JSON.parse(e.data);
      console.log("Agent started:", data.payload.agent_name);
    });

    es.addEventListener("run.end", e => {
      const { status } = JSON.parse(e.data);
      console.log("Run finished:", status);
      es.close();
    });
    ```

=== "Python"

    ```python
    import httpx

    with httpx.stream(
        "GET",
        "https://antcrew.org/runs/abc123/stream",
        headers={"X-Api-Key": "acw_..."},
    ) as r:
        for line in r.iter_lines():
            print(line)
    ```

### Event format

Each event is a standard SSE message:

```
id: 42
event: agent.start
data: {"run_id": "abc123", "event_type": "agent.start", "timestamp": 1754000000.0, "thread_id": "default", "payload": {"agent_name": "pm"}}

id: 43
event: agent.end
data: {"run_id": "abc123", "event_type": "agent.end", "timestamp": 1754000030.5, "thread_id": "default", "payload": {"agent_name": "pm", "cost_usd": 0.0021}}
```

The final message is always a `run.end` event:

```
event: run.end
data: {"run_id": "abc123", "status": "success"}
```

### Replay on reconnect

The `id:` field on each SSE message is the database row primary key. When the client reconnects, the browser (or `EventSource` client) automatically sends the `Last-Event-ID` header with the last received ID. The server replays all events from that point, so no events are lost:

```
GET /runs/abc123/stream
Last-Event-ID: 42         ← server sends events 43, 44, 45… then resumes live
```

This is handled automatically by the browser `EventSource` API. For custom clients, send the header manually:

```bash
curl -N \
  -H "X-Api-Key: acw_..." \
  -H "Last-Event-ID: 42" \
  "https://antcrew.org/runs/abc123/stream"
```

### Completed runs

For runs that have already finished, the stream flushes all events from the database and then sends `run.end` immediately — no polling.

---

## Artifact timeline

Every completed run stores its output artifacts (`code_artifacts`, `test_artifacts`, `doc_artifacts`) in its state. The artifact timeline endpoint aggregates these across runs in chronological order and computes per-file diffs between consecutive versions — giving you a history of how your workspace's artifacts evolved without specifying run-ID pairs manually.

```bash
# Full timeline for the workspace (last 20 completed runs)
GET /runs/artifact-timeline

# Filter to code artifacts only
GET /runs/artifact-timeline?artifact_type=code_artifacts

# Filter to a specific team
GET /runs/artifact-timeline?team=DevTeam&limit=50
```

**Response shape:**

```json
{
  "runs_scanned": 5,
  "versions_with_artifacts": 3,
  "artifact_type": "all",
  "timeline": [
    {
      "version": 1,
      "run_id": "abc123",
      "team": "DevTeam",
      "created_at": "2026-08-20T14:30:00",
      "files_total": 4,
      "files_changed": 4,
      "lines_added": 120,
      "lines_removed": 0,
      "files": [
        { "file_path": "src/app.py", "status": "added", "lines_added": 80, "lines_removed": 0 }
      ]
    },
    {
      "version": 2,
      "run_id": "def456",
      "team": "DevTeam",
      "created_at": "2026-08-21T09:15:00",
      "files_total": 4,
      "files_changed": 1,
      "lines_added": 12,
      "lines_removed": 3,
      "files": [
        { "file_path": "src/app.py", "status": "changed", "lines_added": 12, "lines_removed": 3 },
        { "file_path": "src/utils.py", "status": "unchanged", "lines_added": 0, "lines_removed": 0 }
      ]
    }
  ]
}
```

**File status values:** `added` · `changed` · `unchanged` · `removed`

Runs that produced no artifacts matching the requested type are silently skipped; the `version` counter only increments for runs that contributed artifacts. `runs_scanned` counts all completed runs queried; `versions_with_artifacts` counts how many appear in the timeline.

**Relation to compare-artifacts:** `GET /runs/compare-artifacts?run_a=X&run_b=Y` diffs two specific runs you name. The timeline endpoint automates this across the workspace in chronological order.

**SDK mirror:** The `ArtifactHistory` class in the antcrew SDK provides the same `timeline()` and `record()` API for local CLI workflows, storing versions in a JSON file. The platform endpoint uses the existing `run.state` DB column as its storage — no extra schema is needed.

---

## Run templates

Templates save a run configuration (team, request, cost cap, repo URL) for quick reuse.

```bash
# Save a template
POST /templates/
{ "name": "Auth sprint", "team": "DevTeam", "request": "Build auth module", "max_cost_usd": 2.00 }

# List templates
GET /templates/
```

Templates appear as chips in the **New Run** modal in the dashboard.
