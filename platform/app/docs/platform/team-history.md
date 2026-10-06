# Team governance history & regression detection

`GET /teams/{team}/history` shows a timeline of every configuration change your team has gone through, with the mean eval score for each configuration period. When a change correlates with a score drop ≥10pp, AntCrew surfaces a **regression warning** automatically.

## Why this matters

The governance hash (`compute_team_hash`) tells you *what* changed. Team history tells you *when* it changed and *what it did to quality*. Without this view, a 12-point eval drop is a mystery — with it, you can pinpoint the exact configuration change that caused it.

## How snapshots are created

AntCrew captures a `TeamSnapshot` automatically every time a run completes with a different governance hash than the previous run for the same team. You never have to trigger this manually.

Each snapshot stores:
- The 16-character team governance hash
- The per-agent breakdown (agent name, individual hash, pipeline stage)
- The timestamp it was first seen

## API

### Get team history

```
GET /teams/{team}/history?days=90
```

| Param | Default | Description |
|---|---|---|
| `team` | *(required)* | Team name (e.g. `DevTeam`) |
| `days` | 90 | Lookback window for eval score aggregation |

```json
{
  "team": "DevTeam",
  "snapshots": [
    {
      "snapshot_id": 3,
      "team_hash": "4f2e8a1c9b3d7e5f",
      "label": null,
      "created_at": "2026-08-10T09:00:00",
      "agents": [
        {"agent_name": "BusinessAnalystAgent", "governance_hash": "a3b1c9d2", "stage": "planning"},
        {"agent_name": "BackendDevAgent", "governance_hash": "f7e2a4b8", "stage": "implementation"}
      ],
      "eval_count": 5,
      "avg_score": 0.71
    },
    {
      "snapshot_id": 2,
      "team_hash": "9a1c3f8b2e4d6c7a",
      "label": null,
      "created_at": "2026-08-01T14:30:00",
      "agents": [...],
      "eval_count": 12,
      "avg_score": 0.84
    }
  ],
  "regression_warnings": [
    {
      "team_hash": "4f2e8a1c9b3d7e5f",
      "changed_at": "2026-08-10T09:00:00",
      "previous_hash": "9a1c3f8b2e4d6c7a",
      "score_before": 0.84,
      "score_after": 0.71,
      "drop_pp": 13.0,
      "message": "Score dropped 13.0pp after configuration change on 2026-08-10. Previous stable hash: 9a1c3f8b2e4d6c7a"
    }
  ]
}
```

Snapshots are returned newest first. `eval_count` and `avg_score` cover the period during which that configuration was active (from `created_at` to the next snapshot's `created_at`, or today).

## Regression warnings

A regression warning is emitted when:
- Two consecutive snapshots both have at least 2 eval runs
- The newer configuration's mean score is ≥10pp lower than the older one

The warning names the exact hash that was performing better, so you can compare the two `agents` arrays and identify which agent changed.

## Identifying what changed

Compare the `agents` arrays between the regressed snapshot and the previous stable one:

```python
import requests

h = {"X-Api-Key": "acw_..."}
history = requests.get("/teams/DevTeam/history", headers=h).json()
snapshots = history["snapshots"]

if len(snapshots) >= 2:
    curr_agents = {a["agent_name"]: a["governance_hash"] for a in snapshots[0]["agents"]}
    prev_agents = {a["agent_name"]: a["governance_hash"] for a in snapshots[1]["agents"]}

    for name, curr_hash in curr_agents.items():
        prev_hash = prev_agents.get(name)
        if prev_hash and prev_hash != curr_hash:
            print(f"⚠  {name} changed: {prev_hash} → {curr_hash}")
```

## Relationship to attestation

The `team_hash` in a snapshot is the same value computed by `compute_team_hash()` in the SDK and embedded in run attestations. This means:

- You can correlate a snapshot with any run attestation that shares the same `team_hash`
- If an auditor asks "was the production run using the approved configuration?", compare the attestation's per-agent hashes to the snapshot that was active at that time

See [Run attestation](./attestation.md) and [Governance & provenance](../engine/governance.md) for details.
