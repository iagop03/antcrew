# Reproducible Research Pipeline (UC8)

`ReproducibleResearchPipeline` wraps `ResearchTeam` with mandatory full-trace
recording and a deterministic `team_hash` so every experiment can be cited in
papers and replayed exactly later — even after model updates.

## Why reproducibility matters

AI safety, ML research, and regulated domains require that a peer reviewer can
re-run the exact same pipeline configuration and verify the outputs.  antcrew
provides two anchors:

| Anchor | What it pins |
|--------|-------------|
| `governance_hash` | SHA-256 of a single agent's name + role + stage + tools |
| `team_hash` | Combined hash of all agents — changes if any agent changes |
| `experiment_id` | `"<team_hash>:<run_id>"` — the canonical cite key for a run |

## Quick start

### 1. Record an experiment

```python
from antcrew.teams.reproducible_research import ReproducibleResearchPipeline

pipeline = ReproducibleResearchPipeline(db_path="experiments.db")
exp = pipeline.run("What are the failure modes of multi-agent AI systems?")

print(exp.experiment_id)   # e.g. "a1b2c3d4:550e8400-e29b-41d4-…"
print(exp.team_hash)       # deterministic — cite this in Methods section
print(exp.cost_usd)
```

### 2. Cite in your paper

Include `experiment_id` in the Methods section.  Readers can verify the agent
configuration by checking `team_hash` against any future version of the code:

```python
pipeline = ReproducibleResearchPipeline(db_path="experiments.db")
assert pipeline.team_hash == "a1b2c3d4", "Config drift detected!"
```

### 3. Replay to check for model drift

```python
results = pipeline.replay_experiment(exp.experiment_id)
for r in results:
    print(r["agent_name"], "matched:", r["matched"], "diff:", not r["matched"])
```

`replay_experiment` re-runs every agent call stored in the SQLite trace and
compares the original response to the replayed one.

## Determinism caveats

`replay_experiment` re-runs every agent call against the **live LLM** — it does not inject pre-recorded responses. This means:

| What is reproduced | What is NOT reproduced |
|---|---|
| Agent names, roles, tools, and prompts | Exact LLM output tokens |
| Team structure and execution order | Stochastic sampling (temperature > 0) |
| Cost/token accounting per agent | Responses if the model version changed |

**For bit-exact replay**, pin your pipeline with `temperature=0` and a specific model version (e.g. `model="claude-opus-4-0"` instead of `"claude"`). The `team_hash` verifies agent configuration drift but cannot verify model behavior drift.

!!! info
    The `matched` field in replay results compares semantic equivalence heuristically, not byte equality. A true delta requires human review of the diff.

---

## API reference

### `ReproducibleResearchPipeline`

```python
class ReproducibleResearchPipeline:
    def __init__(
        self,
        model: Optional[BaseLLM] = None,
        trace_log: Optional[TraceLog] = None,   # must have full_trace=True
        db_path: str = "experiments.db",
        agents: Optional[dict] = None,
    ): ...

    @property
    def team_hash(self) -> str: ...

    def run(
        self,
        request: str,
        *,
        thread_id: Optional[str] = None,
    ) -> ExperimentRecord: ...

    def replay_experiment(self, experiment_id: str) -> list[dict]: ...
```

**Notes:**
- If `trace_log` is omitted, a `TraceLog(db_path, full_trace=True)` is created automatically.
- Passing a `TraceLog` with `full_trace=False` raises `ValueError` immediately.
- `replay_experiment` returns the same format as `TraceLog.replay()`.

### `ExperimentRecord`

Frozen dataclass returned by `run()`.

| Field | Type | Description |
|-------|------|-------------|
| `experiment_id` | `str` | `"<team_hash>:<run_id>"` — cite key |
| `run_id` | `str` | UUID of the run in the TraceLog |
| `team_hash` | `str` | SHA-256 of all agents' configurations |
| `request` | `str` | Original research query |
| `cost_usd` | `float` | Total LLM cost for this run |
| `state` | `dict` | Full LangGraph state (contains `research_document`) |

## Detecting config drift in CI

```yaml
# .github/workflows/experiment-reproducibility.yml
- name: Verify agent configuration unchanged
  run: |
    python -c "
    from antcrew.teams.reproducible_research import ReproducibleResearchPipeline
    p = ReproducibleResearchPipeline()
    assert p.team_hash == '${{ vars.APPROVED_TEAM_HASH }}', \
        f'Config drift: {p.team_hash}'
    "
```

## Using an existing TraceLog

```python
from antcrew.trace import TraceLog
from antcrew.teams.reproducible_research import ReproducibleResearchPipeline

# Reuse a shared trace database
tlog = TraceLog("/data/shared/experiments.db", full_trace=True)
pipeline = ReproducibleResearchPipeline(trace_log=tlog)
```

## Related

- [GitHub Action — antcrew regtest](./github-action-regtest.md)
- [Compliance & Governance dashboard](./compliance-dashboard.md)
- [TraceLog SDK reference](../engine/tracelog.md)
