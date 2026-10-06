# White-Label & Agency Billing (UC9)

`WhiteLabelWrapper` lets agencies and consultancies resell antcrew pipelines to
end clients with per-client cost tracking and markup-based billing, without
modifying any agent or team code.

## How it works

```
team.run(request)
        │
        ▼
WhiteLabelWrapper applies markup_pct
        │
        ├─ billed_usd = cost_usd × (1 + markup_pct / 100)
        ├─ margin_usd = billed_usd - cost_usd
        ├─ margin_pct = (margin_usd / billed_usd) × 100
        │
        ▼
BillingRecord returned to agency
        │
        ▼
agency.run_end event  ─── platform logs client_label for GET /runs/margin
```

## Quick start

```python
from antcrew import DevTeam, WhiteLabelWrapper

team = DevTeam()
billing = WhiteLabelWrapper(team, client_label="acme-corp", markup_pct=200)

record = billing.run("Build a REST API for a todo app")

print(f"Cost:   ${record.cost_usd:.4f}")    # raw LLM cost
print(f"Billed: ${record.billed_usd:.4f}")  # what to invoice the client
print(f"Margin: ${record.margin_usd:.4f} ({record.margin_pct:.1f}%)")
```

## Markup percentage semantics

| `markup_pct` | Multiplier | Gross margin |
|-------------|-----------|-------------|
| `0` | 1× | 0 % (pass-through) |
| `100` | 2× | 50 % |
| `200` | 3× | 66.7 % |
| `300` (default) | 4× | 75 % |

## Works with any team

`WhiteLabelWrapper` delegates to `team.run(request, **kwargs)` — any antcrew
team works:

```python
from antcrew import ContentTeam, LegalReviewTeam
from antcrew import WhiteLabelWrapper

# Content agency
content_billing = WhiteLabelWrapper(ContentTeam(), client_label="blog-client", markup_pct=150)

# Legal SaaS reseller
legal_billing = WhiteLabelWrapper(LegalReviewTeam(), client_label="law-firm-a", markup_pct=300)
```

## API reference

### `WhiteLabelWrapper`

```python
class WhiteLabelWrapper:
    def __init__(
        self,
        team: Any,
        *,
        client_label: str,       # non-empty client identifier
        markup_pct: float = 300, # >= 0
    ): ...

    def run(self, request: str, **kwargs) -> BillingRecord: ...
```

**Validation:**
- `client_label` must be a non-empty, non-whitespace string.
- `markup_pct` must be ≥ 0.  Pass `markup_pct=0` for pass-through billing.
- `**kwargs` are forwarded verbatim to `team.run()` (e.g. `thread_id=`).

### `BillingRecord`

| Field | Type | Description |
|-------|------|-------------|
| `client_label` | `str` | Client identifier |
| `cost_usd` | `float` | Raw LLM cost |
| `billed_usd` | `float` | Amount to invoice the client |
| `margin_usd` | `float` | Gross margin in USD |
| `margin_pct` | `float \| None` | Gross margin %; `None` when `billed_usd` is 0 |
| `run_id` | `str` | antcrew run_id |
| `state` | `dict` | Full LangGraph state from the underlying team |

## Margin reporting via the platform

Every `WhiteLabelWrapper.run()` emits `agency.run_end` with `client_label`.
When runs are also sent through the platform API, use `GET /runs/margin` to see
per-client breakdown:

```bash
curl -H "X-Api-Key: $KEY" \
  "https://your-platform/runs/margin?days=30"
# → [{"client_label": "acme-corp", "runs": 12, "cost_usd": 1.23,
#     "billed_usd": 6.00, "margin_usd": 4.77, "margin_pct": 79.5}]
```

## Batch billing example

```python
from antcrew import DevTeam, WhiteLabelWrapper

clients = [
    ("acme-corp",   "Build a payment API",       250),
    ("startup-xyz", "Write a product spec",      150),
    ("law-firm-a",  "Review this NDA contract",  300),
]

for label, task, markup in clients:
    w = WhiteLabelWrapper(DevTeam(), client_label=label, markup_pct=markup)
    rec = w.run(task)
    print(f"{label}: billed ${rec.billed_usd:.2f} (margin {rec.margin_pct:.1f}%)")
```

## Related

- [Compliance & Governance dashboard](./compliance-dashboard.md)
- [Outbound Webhooks](./outbound-webhooks.md)
