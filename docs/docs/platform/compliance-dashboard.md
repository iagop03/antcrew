# Compliance & Governance Dashboard

The Compliance tab in Settings (`/settings?tab=compliance`) gives a live view of
your workspace's audit posture — regulatory mapping, active capabilities, and
one-click attestation downloads for any run.

## Regulatory coverage

| Regulation | Article / Requirement | antcrew capability |
|------------|----------------------|-------------------|
| **HIPAA** | §164.312(b) — Audit Controls | TraceLog NDJSON · Attestation HMAC-SHA256 |
| **GDPR** | Art. 12 — Transparency | `governance_hash` · `EncryptedJSON` |
| **GDPR** | Art. 22 — Automated decisions | HITL structured feedback · per-decision TraceLog |
| **SOC 2** | CC7 — System Operations | Attestation endpoint · offline-verifiable run hash |
| **EU AI Act** | Art. 13 — Transparency | Deterministic `governance_hash` · HITL audit trail |

## Active capabilities

All of the following are available on every workspace with no extra configuration:

| Capability | What it provides |
|-----------|-----------------|
| `governance_hash` | SHA-256 of the agent's name + role + stage + tools — deterministic, config-locked |
| `compute_team_hash()` | Full-team hash; compare before and after a deploy to detect config drift |
| Attestation HMAC-SHA256 | Cryptographic signature per run, exportable and verifiable offline |
| TraceLog NDJSON | Full prompt/response trace exportable to any SIEM (Splunk, Datadog, Elastic) |
| `EncryptedJSON` | Sensitive fields encrypted at rest in trace records |
| HITL audit trail | Structured record of every human decision (feedback schema, reviewer, timestamp) |
| `replay_with_mutation()` | Re-run a historical run with a new prompt; measure output drift |
| SSE event stream | Real-time event stream per run (`GET /runs/{id}/stream`) |
| Outbound webhooks | Push events to external SIEM / decision-monitoring system |
| Per-run cost tracking | Cost attributed by agent and workspace for financial audit |

## Downloading an attestation

### From the dashboard

1. Open **Settings → Compliance**.
2. Select the workspace.
3. Click **Descargar ↓** next to any run.

The attestation JSON contains:

```json
{
  "run_id": "abc123def456",
  "team_name": "DevTeam",
  "governance_hash": "a1b2c3d4",
  "team_hash": "e5f6a7b8",
  "model": "claude-sonnet-4-6",
  "timestamp": "2026-08-22T10:34:21Z",
  "hmac_sha256": "9f3a…",
  "cost_usd": 0.042
}
```

### Via API

```bash
# Single run attestation
curl -H "X-Api-Key: $KEY" \
  https://your-platform/runs/{run_id}/attestation

# Full trace as NDJSON (for SIEM ingestion)
curl -H "X-Api-Key: $KEY" \
  https://your-platform/runs/{run_id}/trace.ndjson
```

### Offline verification

The HMAC-SHA256 signature is computed with your workspace's signing key,
which is included in the `X-Attestation-Key-Id` response header. Verify it
without platform access:

```python
import hmac, hashlib, json

payload = json.dumps({
    "run_id": att["run_id"],
    "governance_hash": att["governance_hash"],
    "timestamp": att["timestamp"],
}, sort_keys=True).encode()

expected = hmac.new(signing_key, payload, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, att["hmac_sha256"])
```

## Detecting config drift

Use `compute_team_hash()` to pin an approved configuration and alert on drift:

```python
from antcrew import compute_team_hash, DevTeam
from antcrew.models.anthropic_model import AnthropicModel

team = DevTeam(model=AnthropicModel())
current_hash = compute_team_hash(team)

APPROVED_HASH = "a1b2c3d4"  # set by your AI review board

if current_hash != APPROVED_HASH:
    raise RuntimeError(f"Config drift detected: {current_hash} != {APPROVED_HASH}")
```

Add this check to your deploy pipeline or run it as a scheduled GitHub Action.

## Compliance Pack

The **Compliance Pack** ($800/month add-on) extends the default capabilities with:

- **Attestation archiving** — long-term retention with tamper-evident storage
- **Managed HMAC signing keys** — key rotation and custody managed by antcrew
- **`compliance.md` export** — auto-generated checklist mapping each run to the
  specific regulatory articles it satisfies, ready for auditor review
- **Priority support** for SOC 2 Type II, HIPAA, and GDPR audit processes

[See pricing →](/pricing#compliance)

## Related

- [GitHub Action — antcrew regtest](./github-action-regtest.md)
- [Outbound Webhooks](./outbound-webhooks.md)
- [TraceLog SDK reference](../engine/tracelog.md)
