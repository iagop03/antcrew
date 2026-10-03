# Governance Hash & Agent Certification

## The problem

Enterprise ML teams spend weeks reviewing and approving agent configurations. After approval, there's no reliable way to verify that the agent running in production is **exactly** the one that was reviewed. Configuration drift is silent and common.

## What is a governance hash?

Every `BaseAgent` computes a deterministic `governance_hash` property:

```python
governance_hash = SHA-256(name + role_description + stage + suffix + tool_names)[:16]
```

- **Deterministic** — same config always produces the same hash
- **Lightweight** — 16-char hex prefix, fits in a table cell
- **Tamper-evident** — any change to name, role, stage, or tools changes the hash

The `team_hash` aggregates all agent hashes into a single value that certifies the entire team composition:

```python
team_hash = "sha256:" + SHA-256(sorted(agent_hashes).join("|"))[:16]
```

## Where to find governance hashes

### In the dashboard

1. Open any completed run
2. Click the **Governance** tab
3. See per-agent hash + team hash
4. Click **copy** to copy any hash to clipboard

### Via API

```http
GET /runs/{run_id}/governance
```

```json
{
  "run_id": "4c3fa8b2...",
  "team": "DevTeam",
  "engine_version": "0.33.22",
  "team_hash": "sha256:a1b2c3d4e5f6789a",
  "agents": [
    {
      "agent_name": "BA",
      "governance_hash": "a1b2c3d4e5f6789a",
      "stage": "analysis",
      "model": ""
    },
    {
      "agent_name": "BackendDev",
      "governance_hash": "b2c3d4e5f6789ab1",
      "stage": "implementation",
      "model": ""
    }
  ]
}
```

### In attestation documents

The attestation JSON (downloadable from the run dashboard) includes `governance_hash` per agent in the `agents` list. The attestation itself is HMAC-signed, making it a cryptographically verifiable proof that a run used a specific configuration.

See [Attestation & Compliance](./attestation.md) for details.

## Certification workflow

**1. After security review: record the approved team hash**

```bash
# Download attestation for the reviewed run
curl -H "X-Api-Key: $KEY" \
  https://app.antcrew.io/runs/$APPROVED_RUN_ID/attestation \
  > approved-config.json

# Extract and store the team hash
jq -r '.agents[] | "\(.agent_name): \(.governance_hash)"' approved-config.json
```

**2. On every production deploy: compare hashes**

Use the CLI for local config verification before deploying:

```bash
antcrew verify-hash team.yaml --expected sha256:a1b2c3d4e5f6789a
# exit 0 → OK to deploy
# exit 1 → config drifted since review
```

Or verify via the platform API after a run completes:

```bash
APPROVED="sha256:a1b2c3d4e5f6789a"
CURRENT=$(curl -s -H "X-Api-Key: $KEY" \
  https://app.antcrew.io/runs/$PROD_RUN_ID/governance \
  | jq -r '.team_hash')

if [ "$CURRENT" != "$APPROVED" ]; then
  echo "ERROR: Team configuration changed since security review!"
  exit 1
fi
echo "OK: Team configuration matches approved hash."
```

**3. In CI/CD: gate on hash equality**

Add a step in GitHub Actions (or any CI) that runs `antcrew verify-hash team.yaml --expected <approved_hash>`. Fail the deploy if they don't match. See [Prompt Regression Testing](./prompt-regression.md#github-actions--full-reusable-workflow) for a complete workflow template that combines both gates.

## Computing hashes locally

### Via CLI (recommended)

```bash
antcrew verify-hash team.yaml
```

Output:

```
  #  Agent        Stage           Governance Hash
  ─────────────────────────────────────────────────────
  1  BA           analysis        a1b2c3d4e5f67890
  2  PM           planning        b2c3d4e5f6789012
  3  BackendDev   implementation  c3d4e5f678901234

Team hash: sha256:9f8e7d6c5b4a3210
3 agents · team.yaml

Tip: pass --expected <hash> to gate a deploy on hash equality.
```

Uses `SimulatedLLM` internally — no API calls, no credentials required.

**Gate a deploy on hash equality:**

```bash
antcrew verify-hash team.yaml --expected sha256:9f8e7d6c5b4a3210
# exit 0 → configuration matches approved baseline
# exit 1 → configuration has changed since approval
```

### Via Python SDK

```python
from antcrew import BaseAgent

agent = BaseAgent(
    name="BA",
    role_description="Business Analyst responsible for requirements.",
    stage="analysis",
)
print(agent.governance_hash)  # e.g. "a1b2c3d4e5f6789a"
```

For a full team:

```python
from antcrew import compute_team_hash, DevTeam

team = DevTeam()
print(compute_team_hash(team))  # SHA-256 hash of all agent hashes
```

## Regulatory mapping

| Requirement | How governance hash helps |
|---|---|
| GDPR Art. 22 — automated decision accountability | Proves which agent version made the decision |
| EU AI Act Art. 13 — transparency | Immutable record of system configuration at decision time |
| SOC 2 CC6.1 — logical access | Verification that only approved configs reach production |
| ISO 27001 A.12.1.2 — change management | Detects unauthorized config changes |

## Related

- [Attestation & Compliance](./attestation.md) — HMAC-signed run provenance
- [Prompt Regression Testing](./prompt-regression.md) — catch prompt changes in CI/CD
- [Compliance Hub](./compliance.md) — full regulatory mapping
