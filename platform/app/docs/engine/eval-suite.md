# EvalSuite — regression testing for agent pipelines

`EvalSuite` is a named, reusable collection of `EvalCase` instances. It wraps `EvalRunner` to make regression testing a one-liner: save a baseline after a good run, then compare every future run against it.

```python
from antcrew.eval import EvalSuite
```

---

## Quickstart

```python
from antcrew.eval import EvalSuite
from antcrew import DevTeam
from antcrew.models.anthropic_model import AnthropicModel

team = DevTeam(model=AnthropicModel())

# 1. Define the suite
suite = EvalSuite.from_requests(
    "auth-flows",
    [
        "Build a login page with JWT auth",
        "Add password reset via email token",
        "Add OAuth2 Google login",
    ],
    expect_min_code_files=1,
)

# 2. Run it once and save as baseline
baseline = suite.run(team)
suite.save_reports(baseline, "baselines/auth-flows.json")

print(suite.summary(baseline))
```

```
AntCrew Eval Summary
======================================================================

[PASS] auth-flows-0
  structural=0.82  tokens=3,241  cost=$0.0041  4230ms
  ...
```

---

## Regression check

On every subsequent run (CI, after a prompt change, after a model upgrade):

```python
baseline = EvalSuite.load_reports("baselines/auth-flows.json")
suite     = EvalSuite.from_eval_reports("auth-flows", baseline)

current = suite.run(team)
ok, diff = suite.regression_check(baseline, current, threshold=0.02)

if not ok:
    print(diff)
    raise SystemExit(1)
```

`regression_check` returns `(True, diff_str)` when no case drops by more than `threshold` (default 0.02). The diff string shows arrows for every case:

```
EvalRunner Comparison (baseline → current)
======================================================================
  [↑] auth-flows-0: 0.72 → 0.84 (+0.12)
  [=] auth-flows-1: 0.68 → 0.69 (+0.01)
  [↓] auth-flows-2: 0.75 → 0.51 (-0.24)   ← regression
      regression: developer.test_coverage 0.80 → 0.42
```

---

## API reference

### Constructors

```python
# From a list of request strings
suite = EvalSuite.from_requests(
    name="sprint-1",
    requests=["Build auth", "Add RBAC"],
    expect_min_code_files=1,      # any EvalCase kwarg
    expect_min_tickets=2,
)

# Re-run the exact cases that produced a baseline
baseline = EvalSuite.load_reports("baselines/sprint-1.json")
suite = EvalSuite.from_eval_reports("sprint-1", baseline)

# Load a saved suite definition
suite = EvalSuite.load("suites/sprint-1.json")
```

### Running

```python
reports = suite.run(team)                    # list[EvalReport]
reports = suite.run(team, judge_llm=llm)     # with LLM-as-judge scoring
```

### Comparing

```python
# Full regression check
ok, diff_str = suite.regression_check(baseline, current, threshold=0.02)

# Just the diff string
diff_str = suite.compare(baseline, current)

# Human-readable table
print(suite.summary(reports))
```

### Persistence

```python
# Save suite definition (cases, not results)
suite.save("suites/sprint-1.json")
suite = EvalSuite.load("suites/sprint-1.json")

# Save / load results for baseline comparisons
suite.save_reports(reports, "baselines/sprint-1.json")
baseline = EvalSuite.load_reports("baselines/sprint-1.json")
```

---

## In CI

```yaml
# .github/workflows/eval.yml
- name: Regression eval
  run: |
    python - <<'EOF'
    from antcrew.eval import EvalSuite
    from antcrew import DevTeam
    from antcrew.models.anthropic_model import AnthropicModel

    team    = DevTeam(model=AnthropicModel())
    suite   = EvalSuite.load("suites/core.json")
    baseline = EvalSuite.load_reports("baselines/core.json")

    current = suite.run(team)
    ok, diff = suite.regression_check(baseline, current)
    print(diff)
    if not ok:
        raise SystemExit("Eval regression detected")
    EOF
```

---

## `EvalCase` expectations

Expectations are optional hard constraints. Violations flip `report.passed` to `False`:

```python
from antcrew.eval import EvalCase

case = EvalCase(
    request="Build a login module",
    name="login-module",
    expect_min_tickets=2,          # pipeline must produce ≥ 2 tickets
    expect_min_code_files=1,       # pipeline must produce ≥ 1 code file
    expect_review_verdict="approve", # reviewer must approve
)
```

`tags` are arbitrary strings for grouping and filtering (no built-in effect):

```python
case = EvalCase("Build RBAC", tags=["auth", "security"])
```

---

## Relationship to EvalRunner

`EvalSuite` is a thin wrapper around `EvalRunner`:

- `suite.run(team)` → `EvalRunner(team).run(cases)`
- `suite.compare(a, b)` → `EvalRunner(None).compare(a, b)`
- `suite.regression_check(a, b)` → compare + threshold filter

Use `EvalRunner` directly when you want fine-grained control over a single run. Use `EvalSuite` when you want versioned baselines and repeatable regression checks.

---

## EvalFeedbackAgent — LLM-powered improvement suggestions

After an eval run, `EvalFeedbackAgent` reads the reports and uses an LLM to produce actionable improvement suggestions per agent:

```python
from antcrew.eval import EvalSuite, EvalFeedbackAgent
from antcrew.models.anthropic_model import AnthropicModel

team    = DevTeam(model=AnthropicModel())
suite   = EvalSuite.from_requests("sprint-1", requests)
reports = suite.run(team)

# Option A: via the suite shorthand
plan = suite.feedback(reports, llm=AnthropicModel())

# Option B: directly
agent = EvalFeedbackAgent(llm=AnthropicModel())
plan  = agent.analyse(reports)

print(plan.priority_action)
# → "PMAgent's ticket quality is the main bottleneck — add an explicit
#    acceptance-criteria format requirement to the system prompt."

for item in plan.agents:
    print(f"\n{item.agent} (score {item.current_score:.2f})")
    print(f"  weak metrics: {', '.join(item.weak_metrics)}")
    for s in item.suggestions:
        print(f"  • {s}")
```

```
pm (score 0.54)
  weak metrics: ticket_quality, acceptance_criteria
  • Add an explicit acceptance-criteria template to the PM system prompt.
  • Require the PM to output at least 3 tickets per feature.

backend_dev (score 0.71)
  weak metrics: test_coverage
  • Instruct the backend agent to always include unit tests alongside implementation.
```

### ImprovementPlan schema

| Field | Type | Description |
|---|---|---|
| `summary` | `str` | One-paragraph overview of the main issues |
| `agents` | `list[AgentImprovement]` | Per-agent breakdown |
| `priority_action` | `str` | Single most impactful change to make first |

**`AgentImprovement`**:

| Field | Type | Description |
|---|---|---|
| `agent` | `str` | Agent name |
| `current_score` | `float` | Mean score across all cases |
| `weak_metrics` | `list[str]` | Metric names below average |
| `suggestions` | `list[str]` | Concrete, actionable suggestions |

### In CI — automated prompt improvement loop

```yaml
# .github/workflows/eval-feedback.yml
- name: Eval + generate improvement plan
  run: |
    python - <<'EOF'
    from antcrew.eval import EvalSuite
    from antcrew import DevTeam
    from antcrew.models.anthropic_model import AnthropicModel

    team    = DevTeam(model=AnthropicModel())
    suite   = EvalSuite.load("suites/core.json")
    reports = suite.run(team)

    ok, diff = suite.regression_check(
        EvalSuite.load_reports("baselines/core.json"), reports
    )
    if not ok:
        plan = suite.feedback(reports, llm=AnthropicModel())
        print("=== Regression detected ===")
        print(plan.priority_action)
        raise SystemExit(1)
    EOF
