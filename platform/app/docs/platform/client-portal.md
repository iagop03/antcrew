# Client portal & margin tracking

AntCrew lets agencies and consultancies run AI pipelines on behalf of multiple clients, track the actual LLM cost per client, and compare it against what they bill — all within a single workspace.

## Concepts

| Term | Meaning |
|---|---|
| `client_label` | Free-text tag on each run identifying which client it belongs to (`"acme"`, `"client-x"`) |
| `billed_usd` | What you invoiced the client for this run (set manually via API) |
| `cost_usd` | Actual LLM cost paid (tracked automatically) |
| Margin | `billed_usd - cost_usd` — your gross profit on the run |
| Viewer key | A read-only API key scoped to a specific `client_label` |

## Tagging runs with a client label

Pass `client_label` when starting a run:

```bash
curl -X POST https://antcrew.org/run/ \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{"team": "DevTeam", "request": "Build auth module", "client_label": "acme"}'
```

All runs for that client will be tagged and visible in margin reports.

## Recording what you billed

```bash
PATCH /runs/{run_id}/billing
Content-Type: application/json

{ "billed_usd": 1.50 }
```

Requires `admin` or `write` role. Returns the updated run object.

## Margin report

```
GET /runs/margin?days=30
```

| Param | Default | Description |
|---|---|---|
| `days` | 30 | Lookback window in days |
| `client_label` | *(all)* | Filter to a specific client |

```json
{
  "period_days": 30,
  "clients": [
    {
      "client_label": "acme",
      "runs": 12,
      "cost_usd": 1.23,
      "billed_usd": 6.00,
      "margin_usd": 4.77,
      "margin_pct": 79.5
    },
    {
      "client_label": "beta-corp",
      "runs": 5,
      "cost_usd": 0.41,
      "billed_usd": null,
      "margin_usd": null,
      "margin_pct": null
    }
  ]
}
```

`billed_usd`, `margin_usd`, and `margin_pct` are `null` when no billing amount has been recorded for runs in that group.

## Viewer keys — client-scoped read-only access

A `viewer` key gives a client read-only access to their own runs only, without exposing other clients' data or internal configuration.

### Create a viewer key

```bash
POST /keys/
{
  "label": "acme-viewer",
  "role": "viewer",
  "client_label": "acme"
}
```

When a request is authenticated with this key:
- `GET /runs/` returns only runs tagged `client_label = "acme"`
- `GET /runs/margin` returns only acme's margin data
- All write operations (POST, PATCH, DELETE) are rejected with 403

### Viewer key permissions

| Action | viewer |
|---|---|
| List runs (own client only) | ✓ |
| Get run detail | ✓ |
| Get run events | ✓ |
| Download artifacts | ✓ |
| Start a run | ✗ |
| Cancel a run | ✗ |
| Set billing amount | ✗ |
| Access other clients' runs | ✗ |

### Client-facing integration

Give the viewer key to your client. They can use it directly with the AntCrew API or integrate it into their own tooling:

```python
import httpx

client = httpx.Client(
    base_url="https://antcrew.org",
    headers={"X-Api-Key": "acw_viewer_..."}
)

# Returns only runs tagged client_label="acme"
runs = client.get("/runs/").json()
```

The client sees their runs, their costs, and their artifacts — nothing else.
