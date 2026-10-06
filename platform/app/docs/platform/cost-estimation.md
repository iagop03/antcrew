# Run cost estimation

`GET /runs/estimate` returns a cost range for a new run before you execute it, based on the last 20 successful runs for the same team.

## Why this matters

AI pipeline costs vary by request complexity, model, and team configuration. Estimation gives teams and their clients an expectation before committing:

- Developers avoid surprises on budget-constrained workspaces
- Agencies can quote clients accurately before running
- Automated systems can gate on cost before dispatching

## API

```
GET /runs/estimate?team=DevTeam
```

| Param | Default | Description |
|---|---|---|
| `team` | *(required)* | Team to estimate for |
| `limit` | 20 | Sample size (5–100 most recent successful runs) |

```json
{
  "team": "DevTeam",
  "based_on_runs": 18,
  "min_usd": 0.0021,
  "median_usd": 0.0038,
  "p75_usd": 0.0055,
  "max_usd": 0.0092
}
```

When there is no history, all cost fields return `null`:

```json
{
  "team": "DevTeam",
  "based_on_runs": 0,
  "min_usd": null,
  "median_usd": null,
  "p75_usd": null,
  "max_usd": null
}
```

## Interpretation

| Field | Meaning |
|---|---|
| `min_usd` | Cheapest run in the sample — simple requests |
| `median_usd` | Typical cost — half of runs cost less than this |
| `p75_usd` | 75th percentile — a comfortable upper bound for budgeting |
| `max_usd` | Most expensive run in the sample — complex requests |

For client quoting, `p75_usd` gives a conservative estimate that covers most real-world runs. The `median_usd` is appropriate for internal cost forecasting.

## Example usage

### Pre-run cost gate (Python)

```python
import httpx

client = httpx.Client(base_url="https://antcrew.org", headers={"X-Api-Key": "acw_..."})

est = client.get("/runs/estimate?team=DevTeam").json()
budget = 0.01  # $0.01 per-run budget

if est["based_on_runs"] > 0 and est["p75_usd"] and est["p75_usd"] > budget:
    raise ValueError(
        f"Estimated run cost (p75 ${est['p75_usd']:.4f}) exceeds budget ${budget:.4f}. "
        "Increase budget or use a smaller model."
    )

# Safe to proceed
client.post("/run/", json={"team": "DevTeam", "request": "..."})
```

### Client quote tool (CLI)

```bash
cost=$(curl -s -H "X-Api-Key: acw_..." \
  "https://antcrew.org/runs/estimate?team=DevTeam" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['p75_usd'] or 'N/A')")

echo "Estimated cost for this run: \$$cost"
```

## How the estimate is computed

The endpoint queries the last `limit` successful runs for the team in your workspace, sorts them by cost, and returns the distribution. Only runs with `status = "success"` and `cost_usd > 0` are included in the sample.

The estimate improves in accuracy as your workspace accumulates more runs. `based_on_runs` tells you how many data points were used — treat estimates based on fewer than 5 runs as rough guidance only.
