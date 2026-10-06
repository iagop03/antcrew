# Prompt Regression Testing

> **"pytest for multi-agent prompts"** — catch prompt regressions before they reach production.

## The problem

When you change a system prompt in a running pipeline, there's no safety net. A prompt tweak that looks harmless can cascade through multiple agents, silently degrading output quality in ways that only surface days later in production.

Traditional unit tests don't help because the outputs are non-deterministic. What you need is **mutation testing against recorded runs**.

## How it works

AntCrew's `replay_with_mutation()` in TraceLog re-executes a historically recorded run with a modified prompt for one agent and quantifies the diff:

1. **Record** — run your pipeline with `--full-trace` to capture every prompt + response
2. **Mutate** — substitute the system prompt for one agent
3. **Replay** — re-run every agent call in order, with the new prompt for the target agent
4. **Diff** — compare the new output against the original using unified diff; compute `diff_pct`
5. **Gate** — exit code 1 if `diff_pct > threshold`

## CLI usage

### Prerequisites

Record a run with full trace enabled:

```bash
antcrew run "Build JWT auth" --team dev --full-trace --trace-db ~/.antcrew/trace.db
```

Get the run ID:

```bash
antcrew trace ~/.antcrew/trace.db
# Shows run list — copy the ID
```

### File-based prompt test

```bash
antcrew regtest ~/.antcrew/trace.db \
  --run <run-id> \
  --agent BA \
  --prompt v2_ba_prompt.txt
```

### Inline prompt test

```bash
antcrew regtest ~/.antcrew/trace.db \
  --run <run-id> \
  --agent BA \
  --prompt-text "You are a Business Analyst. Be extremely concise."
```

### Output

```
antcrew regtest  run=4c3fa8b2…  agent=BA  threshold=20%

 Agent          Mutated  Diff %  Match  Cost
 ─────────────────────────────────────────────
 BA             ✎        34.1%   ✗      $0.0041
 PM             —        —       ✓      $0.0028
 BackendDev     —        —       ✓      $0.0056

✗ FAIL  diff=34.1%  threshold=20%  changed=1/3 agents
```

Exit code `0` on pass, `1` on fail.

### JSON output for scripting

```bash
antcrew regtest trace.db --run $RUN_ID --agent BA --prompt v2.txt --json
```

```json
{
  "run_id": "4c3fa8b2...",
  "agent_name": "BA",
  "threshold": 0.2,
  "diff_pct": 0.341,
  "passed": false,
  "total_changed": 1,
  "calls": [...]
}
```

## CI/CD integration

### GitHub Actions — full reusable workflow

Save as `.github/workflows/prompt-regression.yml` in your repository:

```yaml
name: Prompt regression tests

on:
  pull_request:
    paths:
      - 'prompts/**'
      - '**.yaml'
      - '**.yml'

jobs:
  regression:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install antcrew
        run: pip install antcrew

      - name: Download baseline trace
        env:
          ANTCREW_API_KEY: ${{ secrets.ANTCREW_API_KEY }}
        run: |
          # Download the TraceLog from your platform instance
          curl -fsSL \
            -H "X-Api-Key: $ANTCREW_API_KEY" \
            "https://app.antcrew.io/runs/${{ vars.BASELINE_RUN_ID }}/tracelog" \
            -o baseline.db

      - name: Run prompt regression — BA agent
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          antcrew regtest baseline.db \
            --run "${{ vars.BASELINE_RUN_ID }}" \
            --agent BA \
            --prompt prompts/ba.txt \
            --threshold 0.15

      - name: Run prompt regression — PM agent
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          antcrew regtest baseline.db \
            --run "${{ vars.BASELINE_RUN_ID }}" \
            --agent PM \
            --prompt prompts/pm.txt \
            --threshold 0.20

      - name: Governance hash gate
        run: |
          antcrew verify-hash team.yaml \
            --expected "${{ vars.APPROVED_TEAM_HASH }}"
```

**What to set in your repo:**

| Secret / Variable | Value |
|---|---|
| `secrets.ANTCREW_API_KEY` | API key for the platform instance |
| `secrets.ANTHROPIC_API_KEY` | Provider API key for replaying calls |
| `vars.BASELINE_RUN_ID` | Run ID of the approved baseline |
| `vars.APPROVED_TEAM_HASH` | Team hash from `antcrew verify-hash team.yaml` |

### Recommended workflow

1. Record a **baseline run** after each major prompt release and store the run ID in CI variables (`vars.BASELINE_RUN_ID`)
2. Run `antcrew verify-hash team.yaml` locally, copy the `team_hash`, and store it in `vars.APPROVED_TEAM_HASH`
3. On every PR that touches a prompt file or team YAML, both gates run automatically
4. Set threshold per agent based on sensitivity (BA: 15%, code generators: 25%)
5. Block merge if any agent exceeds its threshold or the governance hash has changed

## Threshold guidelines

| Agent type | Recommended threshold |
|---|---|
| Business Analyst / PM | 10–15% — precise specs, low tolerance |
| Reviewer / QA | 15–20% — structured feedback, moderate |
| Code generators | 20–30% — stylistic variation acceptable |
| Content agents | 25–35% — creative flexibility expected |

## Related

- [`antcrew trace`](../engine/tracelog.md) — inspect TraceLog, show stored prompts
- [Compliance Audit Trail](./compliance.md) — governance hash + attestation
- [Governance Hash](./governance-hash.md) — certify agent configuration identity
