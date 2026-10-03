# Eval health & longitudinal trends

`GET /evals/trends` tracks your agent pipeline quality over time — not just whether a single run passed, but whether your team is getting better or worse across weeks or months.

## Why this matters

A one-off eval score tells you nothing about drift. You change a prompt, re-run, see 0.83 — is that good? You need the baseline. Eval health gives you a rolling view:

- **Average score** over the last N days
- **Pass rate** — what fraction of evals met all expectations
- **Trend** — `improving`, `declining`, or `stable`, computed by comparing the most recent third of data points against the oldest third

The dashboard **Eval health** panel shows the sparkline automatically once you have at least one completed eval.

## API

### Get eval trends

```
GET /evals/trends?team=DevTeam&days=90&limit=200
```

| Param | Default | Description |
|---|---|---|
| `team` | *(all)* | Filter by team name |
| `days` | 90 | Lookback window in days |
| `limit` | 200 | Maximum data points returned |

```json
{
  "team": "DevTeam",
  "days": 90,
  "data": [
    {
      "date": "2026-06-01",
      "eval_id": "abc123",
      "name": "Build JWT auth",
      "team": "DevTeam",
      "overall_score": 0.82,
      "passed": true,
      "cost_usd": 0.0041,
      "elapsed_ms": 14200
    }
  ],
  "summary": {
    "total": 22,
    "avg_score": 0.84,
    "pass_rate": 0.91,
    "passed": 20,
    "failed": 2,
    "trend": "improving"
  }
}
```

Data is returned in chronological order (oldest first) — suitable for direct rendering in time-series charts.

### Trend calculation

`trend` compares the mean `overall_score` of the oldest 33% of data points against the most recent 33%:

| Condition | `trend` |
|---|---|
| New mean > old mean by >3 pp | `"improving"` |
| New mean < old mean by >3 pp | `"declining"` |
| Otherwise | `"stable"` |

Requires at least 6 data points to compute a meaningful trend; returns `"stable"` otherwise.

## Scheduled evals for continuous tracking

Use `EvalSchedule` to run evals automatically on a recurring basis, so the trend chart stays current without manual intervention:

```bash
POST /evals/schedules/
{
  "name": "DevTeam nightly",
  "team": "DevTeam",
  "request": "Build a JWT authentication module with refresh tokens",
  "interval_hours": 24
}
```

Each scheduled run persists its `overall_score` and `passed` state, feeding the trends chart automatically.

## Dashboard panel

The **Eval health** panel appears automatically in the dashboard once at least one eval has been completed. It shows:

- A sparkline of `overall_score` over time (green dots = passed, red = failed)
- Trend badge (↑ Mejorando / ↓ Bajando / — Estable)
- Average score and pass rate for the period
- The 5 most recent evals with individual scores

No configuration required — the panel fetches `GET /evals/trends?days=90` on page load.
