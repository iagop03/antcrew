# Compliance Pack — overview

The antcrew Compliance Pack transforms run history into auditor-ready deliverables
for regulated industries. It adds a browser dashboard for compliance officers, bulk
attestation export, and role-scoped access — on top of the audit infrastructure
already built into every antcrew workspace.

**$299/month · $2,990/year** (≈ 2 months free on annual).
[Campaign pricing available](mailto:legal@antcrew.org) for early adopters and volume deals.

---

## The problem it solves

Every antcrew run already produces a TraceLog and a cryptographically signed attestation
document. Without the pack, exporting those documents requires a developer to call
`GET /runs/{id}/attestation` for each run. A compliance officer — who cannot open a
terminal — has no direct path to the audit evidence they need.

The pack closes that gap:

| Without the pack | With the Compliance Pack |
|---|---|
| One JSON per developer API call | ZIP of up to 500 signed attestations in one click |
| Compliance officer emails a developer | Officer has a dedicated browser portal with their own key |
| Any API key can access billing and admin | `compliance_viewer` keys are hard-scoped to `/compliance/*` |
| No digest unless developer builds one | Daily email digest delivered automatically |

---

## What's included

**Compliance dashboard** — a standalone HTML portal at `GET /compliance/dashboard`.
The officer enters their API key once (stored in `localStorage`, never transmitted).
No installation, no terminal.

**Bulk attestation export** — `GET /compliance/export` returns a ZIP of HMAC-signed
attestation JSONs for up to 500 completed runs. Each file is self-verifiable without
an antcrew dependency — the verification script is embedded in every attestation document.

**`compliance_viewer` role** — API keys with this role return `403` on every endpoint
outside `/compliance/*`. They cannot access billing, admin, or run trigger endpoints.

**Paginated attestation list** — `GET /compliance/attestations` is filterable by status
and paginated, with run cost, team name, and attestation link per row.

**Daily email digest** — compliance officers with an `email` set on their API key
receive a daily summary of completed runs. No pull required.

**Campaign pricing** — platform admin can configure workspace-level pricing for
early adopter deals, startup programs, or reseller agreements without touching list price.

---

## Who it is for

| Vertical | Relevant obligation | How the pack helps |
|---|---|---|
| **FinTech** | SOC 2 | Governance hash = model-config binding for auditors |
| **HealthTech** | HIPAA | Self-hosted + BYOK; no PHI leaves your VPC |
| **LegalTech** | Reproducibility | `experiment_id` citable in briefs; ZIP exportable for filing |
| **Any GDPR Art. 28** | Processor obligations | DPA available; field encryption; data retention controls |

---

## Getting started

See the [technical reference](compliance-pack.md) for the full API specification,
admin setup, and Stripe webhook lifecycle.

For trial access or campaign pricing: [legal@antcrew.org](mailto:legal@antcrew.org)
