# GitHub Action — antcrew regtest

Use `antcrew-ai/antcrew/.github/actions/antcrew-regtest` as a CI/CD gate that fails
a pull request when a prompt change causes the pipeline output to drift beyond a
configurable threshold.

## How it works

The action runs `antcrew regtest` against a TraceLog database recorded with
`--full-trace`. It replays a historical run, substitutes the system prompt for one
agent, and measures the diff in output. If `diff_pct > threshold` the step exits 1
and the workflow fails.

```
recorded run (trace.db)
        │
        ▼
replay_with_mutation(run_id, agent, new_prompt)
        │
        ▼
diff_pct = avg Levenshtein distance across agent calls
        │
diff_pct > threshold? ─── yes ──→ exit 1 (PR blocked)
        │
       no
        │
        ▼
     exit 0 (PR passes)
```

## Quick start

### 1. Record a baseline run

```python
from antcrew import DevTeam
from antcrew.trace import TraceLog

tlog = TraceLog("traces.db")
team = DevTeam(trace_log=tlog)
result = team.run("Build a REST API", full_trace=True)
run_id = result.run_id
print(run_id)  # save this — you'll need it in CI
```

### 2. Add the workflow

```yaml
# .github/workflows/prompt-regression.yml
name: Prompt Regression Gate

on:
  pull_request:
    paths:
      - 'prompts/**'
      - 'agents/**'

jobs:
  regtest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Download trace DB
        uses: actions/download-artifact@v4
        with:
          name: antcrew-traces
          path: .

      - name: Run prompt regression test
        uses: antcrew-ai/antcrew/.github/actions/antcrew-regtest@main
        with:
          db: './traces.db'
          run: ${{ vars.BASELINE_RUN_ID }}
          agent: 'backend_dev'
          prompt-file: './prompts/backend_dev.txt'
          threshold: '0.15'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Print diff result
        if: always()
        run: echo "diff=${{ steps.regtest.outputs.diff-pct }} passed=${{ steps.regtest.outputs.passed }}"
```

### 3. Upload the trace DB as an artifact

```yaml
# In your baseline recording workflow:
- name: Upload traces
  uses: actions/upload-artifact@v4
  with:
    name: antcrew-traces
    path: traces.db
    retention-days: 90
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `db` | No | `antcrew-traces.db` | Path to TraceLog SQLite file |
| `run` | **Yes** | — | Run ID to replay |
| `agent` | **Yes** | — | Agent name whose prompt to replace |
| `prompt-file` | No* | — | Path to file with new system prompt |
| `prompt-text` | No* | — | Inline new system prompt text |
| `threshold` | No | `0.20` | Max allowed diff (0.0–1.0, e.g. `0.15` = 15%) |
| `model` | No | `claude` | LLM alias for replay |
| `antcrew-version` | No | latest | pip version spec, e.g. `==0.33.25` |
| `api-key` | No | env var | Anthropic API key (uses `ANTHROPIC_API_KEY` if empty) |

\* One of `prompt-file` or `prompt-text` is required.

## Outputs

| Output | Description |
|--------|-------------|
| `diff-pct` | Average diff percentage as decimal (e.g. `0.127` = 12.7%) |
| `passed` | `true` if diff ≤ threshold |
| `total-changed` | Number of agent calls whose output changed |

## Threshold guidelines

| Use case | Recommended threshold |
|----------|-----------------------|
| Strict compliance / regulated output | `0.05` (5%) |
| Standard product pipeline | `0.15`–`0.20` (15–20%) |
| Creative / marketing content | `0.30`–`0.40` (30–40%) |
| Smoke test only | `0.50` (50%) |

## Requirements

- The baseline run must have been recorded with `full_trace=True`
- An Anthropic API key (or compatible provider) for the LLM replay calls
- antcrew ≥ 0.33.25 installed in the runner

## Related

- [Eval suite reference](../engine/eval-suite.md)
- [TraceLog documentation](../engine/tracelog.md)
- [Compliance & Governance dashboard](./compliance-dashboard.md)
