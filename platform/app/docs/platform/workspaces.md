# Workspaces & API Keys

## Workspaces

A workspace is an isolated tenant. Everything — runs, tickets, reviews, provider keys — belongs to a workspace.

## Members & roles

| Role | Capabilities |
|---|---|
| `owner` | Full access including billing and member management |
| `admin` | All workspace settings, no billing |
| `member` | Create runs, view tickets, submit reviews |
| `reviewer` | View and action HITL reviews only |

## API Keys

Each workspace has one or more API keys. Use them in the `X-Api-Key` header for all REST API requests:

```bash
curl https://antcrew.org/run/ \
  -H "X-Api-Key: acw_live_..." \
  -H "Content-Type: application/json" \
  -d '{"team": "DevTeam", "request": "..."}'
```

Pass the key to `EventBusBridge` when streaming engine events to the platform:

```python
from antcrew_engine.engine import EventBusBridge

bridge = EventBusBridge(
    platform_url="https://antcrew.org",
    api_key="acw_live_...",
    run_id="run_abc123",
)
```

Keys can be rotated at any time from **Settings → API Keys** without downtime.

## Provider keys (BYOK)

Add your own LLM provider API keys in **Settings → Providers**. Keys are encrypted at rest. The proxy uses them to make calls on your behalf — your application code never sees them.

---

## Workspace analytics

`GET /workspaces/{id}/analytics` returns a 30-day usage breakdown with no query parameters required.

```bash
GET /workspaces/42/analytics
```

```json
{
  "workspace_id": 42,
  "total_cost_usd": 14.32,
  "runs_by_day": [
    { "date": "2026-08-01", "count": 8, "success": 7, "failed": 1, "cost": 1.24 }
  ],
  "by_team": [
    { "team": "DevTeam", "runs": 24, "tickets": 68, "cost_usd": 9.80 },
    { "team": "ResearchTeam", "runs": 6, "tickets": 0, "cost_usd": 4.52 }
  ],
  "by_model": [
    { "model": "claude:claude-sonnet-5", "runs": 28, "cost_usd": 12.10 },
    { "model": "groq:llama-3.3-70b-versatile", "runs": 2, "cost_usd": 2.22 }
  ],
  "by_agent": [
    { "agent_name": "BackendDevAgent", "invocations": 24, "tokens_in": 48200, "tokens_out": 12400, "cost_usd": 4.82 },
    { "agent_name": "BusinessAnalystAgent", "invocations": 24, "tokens_in": 36100, "tokens_out": 9800, "cost_usd": 3.61 }
  ],
  "tickets_by_status": { "open": 42, "in_progress": 18, "done": 8 }
}
```

`by_agent` is sorted by `cost_usd` descending and sourced from the `agent_event` table, which captures per-invocation token and cost data from `agent.end` events. Returns an empty list until the first run with agent event recording completes.

---

## Budget alerts

When `max_cost_usd` is set on a workspace, the platform checks spend hourly and fires a Slack notification at **80%** and **100%** of the limit:

- Configure the Slack webhook in **Settings → Notifications → Slack** or via `PATCH /workspaces/{id}/slack`
- At 100%, new runs are rejected with a 422 until the limit is raised
- Alerts fire once per process restart (not once per hour) to avoid flooding

---

## Message-count limit

Long-running pipelines accumulate LLM messages in `TeamState`. Left uncapped, context windows grow unboundedly and drive up cost per run. Set `max_messages` to trim the message list to the most recent N entries after each agent step.

**Workspace default** (applies to all teams in the workspace):

```bash
PATCH /workspaces/{id}/max-messages
Content-Type: application/json
{"max_messages": 100}
```

Pass `null` to remove the limit.

**Per-team override** (overrides the workspace default for one team type):

```bash
# Create or update a preset for DevTeam with its own limit
PATCH /workspaces/{id}/presets/{preset_id}
{"max_messages": 60}
```

Priority: team preset `max_messages` > workspace `max_messages` > env `ANTCREW_MAX_MESSAGES` > unlimited.

The limit is also configurable via the `ANTCREW_MAX_MESSAGES` environment variable for self-hosted deployments without a DB.

> **Note:** Trimming keeps the *most recent* N messages, so agents always see the latest context. Earlier messages (initial system setup, old intermediate results) are discarded first.
