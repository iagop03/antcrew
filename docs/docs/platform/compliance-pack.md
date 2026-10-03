# Compliance Pack

**Landing page:** `antcrew.org/compliance-pack` — share this with your compliance officer or decision-maker. No account required to view pricing.

The Compliance Pack is a paid add-on that transforms antcrew-platform into a compliance-grade AI operations hub. It adds a central dashboard for compliance officers (non-devs), bulk attestation export, and workspace-level pricing — on top of the audit infrastructure already built into the SDK.

---

## What the pack includes

| Capability | How it works |
|---|---|
| **Compliance dashboard** | `GET /compliance/dashboard` — standalone HTML portal; compliance officers open it in a browser, enter their API key once (stored in localStorage), and browse attestations with pagination and a one-click ZIP export. No terminal required. |
| **compliance_viewer role** | API keys created with `role=compliance_viewer` are hard-restricted to `/compliance/*` paths. They cannot call run, billing, or admin endpoints. |
| **Paginated attestation list** | `GET /compliance/attestations` — JSON list of all runs with status, cost, team, and attestation link. |
| **Bulk export (ZIP)** | `GET /compliance/export` — downloads a ZIP of HMAC-signed attestation JSONs for the N most recent completed runs. Submit directly to an auditor. |
| **Self-serve checkout** | `POST /compliance/checkout` — creates a Stripe Checkout session for monthly or annual billing. Pack auto-enables after payment via webhook. |
| **Daily email digest** | Compliance officers with an `email` set on their API key receive a daily digest of runs completed in the last 24 hours. |
| **Pack status & pricing** | `GET /compliance/status` — returns whether the pack is active and the effective pricing for the workspace. |
| **Admin toggle** | `PATCH /admin/workspaces/{id}` — admin can enable/disable the pack per workspace and set workspace-level price overrides. Also available as a one-click button in the admin panel workspace table. |
| **Platform default pricing** | `PATCH /admin/billing-rates` — set the platform-wide default monthly/annual price for the pack. |

The underlying audit infrastructure (TraceLog, governance hash, `GET /runs/{id}/attestation`, HMAC signing) is available to all workspaces regardless of pack status. The pack adds the **aggregation, bulk export, and non-dev access layer** that turns raw audit data into a compliance artifact.

---

## Why two tiers

| SDK + platform (no pack) | SDK + platform + Compliance Pack |
|---|---|
| `GET /runs/{id}/attestation` downloads one signed JSON | `GET /compliance/export` downloads all signed JSONs as a ZIP |
| Developer reads traces in terminal | Compliance officer reads a dashboard at `/compliance/attestations` |
| HMAC signed by platform key | Same — plus bulk export with workspace-stamped filename |
| Self-serviced audit | Auditor-ready deliverable without involving a developer |

The critical gap: a compliance officer cannot open a terminal. The pack gives them a portal.

---

## Admin: enabling the pack

```bash
# Enable pack for a workspace, set workspace-specific pricing
PATCH /admin/workspaces/{workspace_id}
{
  "compliance_pack_enabled": true,
  "compliance_pack_price_monthly": 79.0,     # null → use platform default
  "compliance_pack_price_annual": 790.0
}
```

Passing `null` for price fields clears the workspace override and falls back to the platform default.

### Set platform-wide default pricing

```bash
PATCH /admin/billing-rates
{
  "compliance_pack_price_monthly": 49.0,
  "compliance_pack_price_annual": 490.0
}
```

Default: **$49/month · $490/year** (≈ 2 months free on annual).

---

## Compliance officer setup

Compliance officers are non-developers who need read-only access to the audit trail. Set them up with a `compliance_viewer` API key:

```bash
# Admin creates a restricted API key for the compliance officer
POST /api-keys
{
  "name": "Compliance Officer – Jane Smith",
  "role": "compliance_viewer",
  "email": "jane@example.com"    # enables daily digest
}
```

The officer:

1. Opens `https://your-platform.com/compliance/dashboard` in a browser.
2. Enters the API key once — it is stored in localStorage and never sent to a third party.
3. Browses paginated attestations and clicks **Export ZIP** to download the signed archive.

`compliance_viewer` keys return `403` for every endpoint outside `/compliance/*`.

---

## Self-serve checkout

To purchase the Compliance Pack without admin intervention:

```bash
POST /compliance/checkout
Authorization: X-Api-Key <any non-viewer key>
{ "billing_cycle": "monthly" }   # or "annual"
```

Response:

```json
{ "checkout_url": "https://checkout.stripe.com/..." }
```

Redirect the user to `checkout_url`. After payment, Stripe fires `checkout.session.completed` and the platform automatically sets `compliance_pack_enabled = true` for the workspace. Returns `409` if the pack is already enabled.

Required env vars on the server: `STRIPE_SECRET_KEY`, `STRIPE_COMPLIANCE_PRICE_MONTHLY_ID`, `STRIPE_COMPLIANCE_PRICE_ANNUAL_ID`, `STRIPE_SUCCESS_URL`, `STRIPE_CANCEL_URL`.

### Subscription lifecycle

The platform listens to `customer.subscription.updated` and `customer.subscription.deleted` events for Stripe subscriptions that carry `metadata.pack = "compliance"`. Status transitions:

| Stripe status | Pack state |
|---|---|
| `active` / `trialing` | Enabled |
| `past_due` / `unpaid` / `canceled` | **Disabled immediately** |
| `deleted` event | Disabled |

Payment failure → `past_due` → pack disabled. When payment recovers, `active` → pack re-enabled automatically.

---

## API reference

### `GET /compliance/dashboard`

Returns a standalone HTML page. No pack-enabled check — always accessible so the officer can see the upgrade prompt when the pack is not yet active. Pass the API key via the browser UI (not a query parameter).

---

### `GET /compliance/status`

Returns pack status and effective pricing for the current workspace. Always accessible — use it to show an upgrade prompt when `enabled=false`.

```json
{
  "enabled": true,
  "workspace_price_monthly": 79.0,
  "workspace_price_annual": 790.0,
  "platform_price_monthly": 49.0,
  "platform_price_annual": 490.0
}
```

`workspace_price_*` is `null` when no override is set — the platform default applies.

---

### `GET /compliance/attestations`

Requires pack enabled (returns `402` otherwise).

| Query param | Default | Description |
|---|---|---|
| `limit` | `50` | Max results (max `200`) |
| `offset` | `0` | Pagination offset |
| `status` | — | Filter by run status (`done`, `error`, `running`…) |

```json
[
  {
    "run_id": "a3f4b9c1...",
    "team": "LegalReviewTeam",
    "status": "done",
    "cost_usd": 0.43,
    "created_at": "2026-08-22T14:32:11Z",
    "finished_at": "2026-08-22T14:32:45Z",
    "attestation_url": "/runs/a3f4b9c1.../attestation"
  }
]
```

---

### `GET /compliance/export`

Requires pack enabled (returns `402` otherwise). Returns `404` if no completed runs exist.

| Query param | Default | Description |
|---|---|---|
| `limit` | `100` | Max completed runs to include (max `500`) |

Returns a `application/zip` download named `compliance_export_{slug}_{timestamp}.zip`.

Each file inside is `attestation-{run_id[:12]}.json` and contains:

```json
{
  "schema_version": "1.0",
  "run_id": "...",
  "team": "LegalReviewTeam",
  "agents": [...],
  "governance_hash": "sha256:...",
  "document_hash": "sha256:...",
  "hmac_sha256": "hmac-sha256:..."   // present when ATTESTATION_HMAC_SECRET is set
}
```

The auditor can verify `document_hash` independently:

```python
import hashlib, json

doc = json.load(open("attestation-a3f4b9c1.json"))
body = {k: v for k, v in doc.items() if k not in ("document_hash", "hmac_sha256")}
assert doc["document_hash"] == "sha256:" + hashlib.sha256(
    json.dumps(body, sort_keys=True).encode()
).hexdigest()
```

---

## Infrastructure already included (no pack required)

These features are available to every workspace regardless of pack status:

| Feature | Endpoint / CLI |
|---|---|
| Single-run signed attestation | `GET /runs/{run_id}/attestation` |
| Per-agent governance hash | `GET /runs/{run_id}/governance` |
| BYOK key isolation | keybridge — [setup](../proxy/index.md) |
| Field encryption at rest | Fernet AES-128-CBC — [reference](encryption.md) |
| TraceLog (SDK, local) | `antcrew inspect <run-id>` |
| `--compliance` flag | `antcrew run --compliance` |
| Trace replay | `antcrew trace replay <run-id>` |

---

## Compliance checklist by vertical

| Use case | Required | Notes |
|---|---|---|
| **FinTech (SOC 2)** | Pack + attestation + TraceLog + field encryption | Governance hash = model-config binding for audit. |
| **LegalTech** | Pack + ReproducibleResearchPipeline | Cite `experiment_id` in legal research. Export ZIP for file. |
| **HealthTech (HIPAA)** | Self-hosted + BYOK/keybridge + TraceLog | No PHI in prompts. Ollama for fully on-prem inference. |
| **GDPR Art. 28** | Self-hosted or managed + DPA | Managed: antcrew is Processor. Self-hosted: you are Controller + Processor. |

For the full DPA, contact legal@antcrew.org.
