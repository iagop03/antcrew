# Agent Benchmarks

antcrew tracks performance metrics for every run and aggregates them into public baselines. The Benchmarks page lets you compare your workspace's actual costs and completion times against these baselines — and compare antcrew against other frameworks on the same task types.

## Viewing benchmarks

Navigate to **Benchmarks** in the sidebar (or `GET /benchmarks` from a browser). The page is public — no authentication required.

### Summary metrics

Four headline numbers across the full benchmark suite:

| Metric | Description |
|--------|-------------|
| Tasks benchmarked | Total distinct task definitions in the suite |
| Avg cost / task | Mean USD cost across all task types |
| Avg completion | Mean wall-clock seconds from `pipeline.start` to `pipeline.end` |
| Pass rate | % of runs that produced all expected output keys without errors |

### Filtering and sorting

Use the category filter bar to narrow to: Engineering, Research, Compliance, Content, or Data. Sort by cost, duration, pass rate, or token count.

## Benchmark data

Each row in the table covers one (task, team) combination and shows:

- **Cost** — mean `cost_usd` from `pipeline.end` events, plus a proportional bar
- **Duration** — mean wall-clock seconds
- **Tokens in / out** — mean prompt and completion tokens
- **Pass rate** — % of runs that completed without errors and returned required fields

### Standard task suite

| Task | Team | Avg cost | Pass rate |
|------|------|----------|-----------|
| Code review | DevTeam | $0.0042 | 97% |
| Feature planning | FeatureTeam | $0.0071 | 95% |
| Full-stack feature | FullStackTeam | $0.0118 | 91% |
| Code migration | CodeMigrationTeam | $0.0089 | 88% |
| Research summary | ResearchTeam | $0.0055 | 96% |
| Legal review | LegalReviewTeam | $0.0143 | 94% |
| Content generation | ContentTeam | $0.0029 | 99% |
| Webhook processing | WebhookSink | $0.0003 | 100% |

## Cost by team type

The Benchmarks page shows cost and pass-rate data for each antcrew team type across the "code review" and "feature planning" task types. Numbers reflect real provider calls with the default cost routing policy.

antcrew's per-run cost comes from [cost routing](cost-routing.md): cheap models handle formatting, validation, and extraction; premium models run only for planning, architecture, and legal review.

## Cost by routing tier

When `cost_routing_policy = auto`, agent invocations split across three tiers:

| Tier | Avg cost / call | Agents |
|------|----------------|--------|
| cheap | $0.0001 | validator, formatter, extractor, router |
| standard | $0.0018 | developer, reviewer, researcher, writer |
| premium | $0.0082 | architect, planner, legal, auditor |

## Methodology

Benchmarks run in two modes:

1. **CI mode** — `SimulatedLLM` with deterministic responses. Used to measure pass rate and structural correctness.
2. **Real mode** — actual provider models. Used to measure cost, duration, and token counts.

**Pass rate** is the fraction of runs that completed all stages and returned all expected output keys without uncaught exceptions.

**Duration** is wall-clock seconds from the `pipeline.start` event timestamp to the `pipeline.end` event timestamp.

**Cost** is the `cost_usd` field on the `pipeline.end` event payload, which sums `cost_usd` from all `agent.end` events in the run.

### Running the benchmark suite locally

```bash
antcrew benchmark run --suite standard
```

Requires `antcrew` CLI ≥ 0.33 and a configured workspace. Outputs NDJSON to stdout and a summary table to stderr.

To run a single task:

```bash
antcrew benchmark run --task "code_review" --team DevTeam --runs 10
```

## API

The benchmark data is embedded as static JSON in `benchmarks.html`. There is no dedicated REST endpoint — benchmark data is updated when the page is regenerated as part of CI.

To access current baseline metrics programmatically, use the run event API to aggregate your own workspace's data:

```bash
GET /runs?workspace_id={id}&limit=500
```

Then aggregate `pipeline.end.payload.cost_usd` and `pipeline.end.payload.duration_s` across runs grouped by team name.
