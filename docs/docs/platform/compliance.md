# Compliance Pack

The Compliance Pack is an add-on for teams operating in regulated industries — healthcare (HIPAA), financial services (SOC 2, PCI-DSS), and legal-tech (GDPR Art. 17, ISO 27001). It bundles auditable attestation documents, an encrypted TraceLog reference, a compliance officer dashboard, and role-based access into a single workspace-level add-on.

---

## What's included

| Feature | Description |
|---|---|
| **Signed attestation documents** | Every run produces a HMAC-signed JSON document (schema 1.1) with agent governance hashes, cost, timestamps, and a SHA-256 digest of the TraceLog. |
| **TraceLog reference** | When the engine emits a `tracelog` in the run state, the attestation includes a `tracelog_ref` with a tamper-evident SHA-256 digest. Auditors can verify the log was not modified without seeing its contents. |
| **ZIP export** | Download all completed runs as a ZIP of signed attestation files in one click, ready to hand to an auditor. |
| **Compliance dashboard** | A standalone HTML page (`/compliance/dashboard`) that non-developer compliance officers can access directly with a `compliance_viewer` API key — no developer tooling required. |
| **`compliance_viewer` role** | A read-only API key role scoped to `/compliance/*` paths only. Issue one per auditor; revoke on offboarding. |
| **Weekly digest emails** | `compliance_viewer` keys with an email address receive a weekly summary of completed runs automatically. |
| **Stripe billing** | Enabled via `POST /compliance/checkout`. The pack activates automatically after Stripe confirms payment. Cancellation disables it instantly. |

---

## Enabling the pack

### Via Stripe checkout (production)

```bash
POST /compliance/checkout
Authorization: X-Api-Key <admin_key>

{"billing_cycle": "monthly"}   # or "annual"
```

Returns `{"checkout_url": "https://checkout.stripe.com/..."}`. After payment, the platform webhook enables the pack automatically.

### Via admin API (internal / dev)

```bash
PATCH /admin/workspaces/{id}
Authorization: Bearer <PLATFORM_ADMIN_TOKEN>

{"compliance_pack_enabled": true}
```

---

## Attestation document (schema 1.1)

```json
{
  "schema_version": "1.1",
  "run_id": "abc123",
  "team": "LegalReviewTeam",
  "request_preview": "Review contract clause 7.3...",
  "status": "success",
  "cost_usd": 0.048,
  "workspace_id": 12,
  "created_at": "2026-08-22T14:00:00+00:00",
  "finished_at": "2026-08-22T14:02:31+00:00",
  "agents": [
    {
      "agent_name": "LegalReviewer",
      "governance_hash": "sha256:abc...",
      "stage": "review",
      "duration_s": 18.4,
      "tokens_in": 2100,
      "tokens_out": 850,
      "cost_usd": 0.048
    }
  ],
  "tracelog_ref": {
    "algorithm": "sha256",
    "digest": "sha256:def...",
    "entries": 4
  },
  "platform_version": "antcrew-platform",
  "engine_version": "0.34.0",
  "attestation_generated_at": "2026-08-22T14:02:32+00:00",
  "document_hash": "sha256:ghi...",
  "hmac_sha256": "hmac-sha256:jkl..."
}
```

### Verifying integrity offline

```python
import hashlib, json, hmac

doc = json.load(open("attestation-abc123.json"))

# 1. Verify document_hash (SHA-256 of body without hash fields)
body = {k: v for k, v in doc.items() if k not in ("document_hash", "hmac_sha256")}
expected_hash = "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
assert doc["document_hash"] == expected_hash, "document_hash mismatch — file may have been tampered"

# 2. Verify HMAC (requires ATTESTATION_HMAC_SECRET)
secret = b"your-secret"
body_with_hash = {k: v for k, v in doc.items() if k != "hmac_sha256"}
expected_hmac = "hmac-sha256:" + hmac.new(
    secret, json.dumps(body_with_hash, sort_keys=True).encode(), "sha256"
).hexdigest()
assert doc["hmac_sha256"] == expected_hmac, "HMAC invalid — document not issued by this server"
```

---

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/compliance/status` | any key | Pack enabled/disabled + effective pricing |
| `GET` | `/compliance/dashboard` | any key | Compliance officer HTML dashboard |
| `GET` | `/compliance/attestations` | admin or compliance_viewer | Paginated list of runs with attestation URLs |
| `GET` | `/compliance/export` | admin or compliance_viewer | ZIP of signed attestation JSONs for all completed runs |
| `POST` | `/compliance/checkout` | admin | Create Stripe Checkout Session for the pack |
| `GET` | `/runs/{run_id}/attestation` | admin or compliance_viewer | Individual attestation download |

---

## Required environment variables

| Variable | Description |
|---|---|
| `ATTESTATION_HMAC_SECRET` | Strong random secret for HMAC signing. Generate: `openssl rand -hex 32`. Without it, attestations include `document_hash` only (no HMAC). |
| `STRIPE_COMPLIANCE_PRICE_MONTHLY_ID` | Stripe Price ID for monthly billing. |
| `STRIPE_COMPLIANCE_PRICE_ANNUAL_ID` | Stripe Price ID for annual billing. |

---

## Setting workspace-level price overrides

Platform admins can override the default compliance pack price per workspace:

```bash
PATCH /admin/workspaces/{id}
{"compliance_pack_price_monthly": 79.0, "compliance_pack_price_annual": 790.0}
```

Default prices are set via `PATCH /admin/billing-rates`:

```bash
PATCH /admin/billing-rates
{"compliance_pack_price_monthly": 49.0, "compliance_pack_price_annual": 490.0}
```

---

## Issuing compliance_viewer keys

```bash
POST /api-keys/
Authorization: X-Api-Key <admin_key>

{
  "label": "External Auditor — KPMG",
  "role": "compliance_viewer",
  "email": "auditor@kpmg.example"
}
```

The key is returned once. The auditor can access `/compliance/*` paths only. Add `"email"` to receive weekly digest emails. Revoke with `DELETE /api-keys/{id}`.

---

## Certified Agent (UC3)

The Certified Agent flow lets you register an **approved governance hash** for each agent and then verify on every run that the agent configuration was not modified.

### How governance hashes work

Every agent emits a `governance_hash` — a deterministic SHA-256[:16] of its name, role, model, stage, and tool list — in the `agent.end` event. Identical hashes across runs prove the agent config was unchanged.

### Register an approved hash

```bash
POST /compliance/approved-hashes
Authorization: X-Api-Key <admin_key>

{
  "team": "DevTeam",
  "agent_name": "BusinessAnalyst",
  "governance_hash": "a1b2c3d4e5f60001",
  "label": "v1.2 approved by security team"
}
```

Returns `201` with the registered record. Only one active hash per (team, agent_name) is allowed; register a second one returns `409` — delete the existing hash first.

### List and manage approved hashes

```bash
# List all active approved hashes
GET /compliance/approved-hashes
GET /compliance/approved-hashes?team=DevTeam

# Soft-delete (retains record for audit history)
DELETE /compliance/approved-hashes/{id}
```

### Download a run certificate

```bash
GET /runs/{run_id}/certificate
```

Returns a signed JSON certificate (attachment: `certificate-{run_id[:12]}.json`) with:

```json
{
  "schema_version": "1.0",
  "certificate_type": "agent_certification",
  "run_id": "abc123def456",
  "team": "DevTeam",
  "certification_status": "certified",
  "agents": [
    {
      "agent_name": "BusinessAnalyst",
      "governance_hash": "a1b2c3d4e5f60001",
      "approved_hash": "a1b2c3d4e5f60001",
      "status": "certified"
    },
    {
      "agent_name": "BackendDev",
      "governance_hash": "badcafe00000001",
      "approved_hash": "deadbeef0000001",
      "status": "drifted"
    }
  ],
  "document_hash": "sha256:...",
  "hmac_sha256": "hmac-sha256:..."
}
```

**`certification_status` values:**

| Value | Meaning |
|---|---|
| `certified` | All agents with registered hashes matched — config was not modified |
| `drifted` | One or more agents produced a hash different from the approved one |
| `unchecked` | No approved hashes registered for this team — certification not possible |

---

## HITL decisions in TraceLog

When a HITL review is resolved (approved or rejected) via `POST /reviews/{review_id}`, the platform automatically records the decision in the active TraceLog database. This creates a tamper-evident audit trail of every human oversight action linked to the same SQLite DB as the LLM call records.

**Schema (`hitl_decisions` table):**

| Column | Type | Description |
|---|---|---|
| `run_id` | TEXT | Links to the `runs` table |
| `step` | TEXT | Agent or pipeline step that triggered the review (e.g. `backend_dev`) |
| `decision` | TEXT | `approved`, `rejected`, or a custom verdict |
| `reviewer_id` | TEXT | User identifier (email, Slack user id, or API key label) |
| `reason` | TEXT | Optional free-text explanation from the reviewer |
| `decided_at` | TEXT | ISO timestamp of when the decision was recorded |

**Read via SDK:**

```python
from antcrew.trace import TraceLog

trace = TraceLog("./antcrew_trace.db")
decisions = trace.get_hitl_decisions(run_id)
# [{"step": "backend_dev", "decision": "approved", "reviewer_id": "alice@co", ...}]
```

**Record manually** (for custom HITL integrations):

```python
trace.record_hitl(
    run_id=run_id,
    step="legal_review",
    decision="approved",
    reviewer_id="legal@company.com",
    reason="Reviewed for GDPR compliance",
)
```

The `hitl_decisions` table is created automatically when an existing TraceLog DB is opened (no migration needed — the `_migrate()` function creates it on first access).

The certificate does not require the Compliance Pack to be enabled — any workspace can request it. The `document_hash` field (SHA-256 of the document body) lets auditors verify the file was not tampered with. When `ATTESTATION_HMAC_SECRET` is configured, `hmac_sha256` provides an additional platform-signed proof.

### Typical workflow

1. Run `DevTeam` in a controlled environment and note the governance hashes from `GET /runs/{id}/governance`.
2. Security team approves the hashes: `POST /compliance/approved-hashes` for each agent.
3. In production, download `GET /runs/{id}/certificate` after each run.
4. `certification_status: certified` → config unchanged. `drifted` → investigate the agent that changed.
