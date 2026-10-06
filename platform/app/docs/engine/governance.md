# Governance & run provenance

AntCrew tracks a **governance hash** for every agent and team. Two runs with the same hash used identical configurations — same prompts, same tools, same stage layout — so you can compare them as apples-to-apples.

```python
from antcrew.core.agent import compute_team_hash

hash_before = compute_team_hash(team.agents)
# ... update a system prompt ...
hash_after = compute_team_hash(team.agents)

if hash_before != hash_after:
    print("Config changed — baseline comparison is invalid until re-baselining")
```

---

## Agent stage

`stage` is an optional string label that groups agents into named pipeline phases. It has no runtime effect — it is used by AntCrew Studio to group agent nodes visually and by governance hashing to encode pipeline structure:

```python
from antcrew.core.agent import BaseAgent

class PMAgent(BaseAgent):
    name = "pm"
    stage = "planning"          # ← pipeline phase label
    role_description = "..."

class BackendDevAgent(BaseAgent):
    name = "backend_dev"
    stage = "implementation"

class QAAgent(BaseAgent):
    name = "qa"
    stage = "verification"
```

Common stage labels (arbitrary — use whatever fits your pipeline):

| Stage | Typical agents |
|---|---|
| `planning` | PMAgent, DiscoveryAgent, SprintPlannerAgent |
| `implementation` | BackendDevAgent, FrontendDevAgent, DevOpsAgent |
| `verification` | QAAgent, ReviewerAgent, SecurityAgent |
| `documentation` | DocWriterAgent |

---

## Agent governance hash

`BaseAgent.governance_hash` is a 16-character hex string (truncated SHA-256) computed from the agent's immutable configuration:

| Input | What's included |
|---|---|
| `name` | Agent class identifier |
| `role_description` | Class-level role string |
| `stage` | Pipeline phase label |
| `system_prompt_suffix` | Per-instance prompt override |
| `tools` | Sorted list of tool names |

```python
agent = BackendDevAgent(llm=llm)
print(agent.governance_hash)   # e.g. "3a7f91c0b2e54d8a"

# Per-instance suffix changes the hash
agent_with_suffix = BackendDevAgent(
    llm=llm,
    system_prompt_suffix="Always use TypeScript.",
)
print(agent_with_suffix.governance_hash)  # different hash
```

The hash is **deterministic**: the same configuration always produces the same value regardless of when or where it runs.

---

## Team governance hash

`compute_team_hash(agents)` combines individual agent hashes (sorted by name) into a single team-level hash. Use it for run-level provenance:

```python
from antcrew.core.agent import compute_team_hash
from antcrew import DevTeam

team = DevTeam(model=llm)
team_hash = compute_team_hash(team.agents)
print(team_hash)  # e.g. "c1d4e2f3a5b67890"
```

### In CI — detect config drift

```python
import json, pathlib

SNAPSHOT_PATH = pathlib.Path("governance/team_hash.json")

def check_governance_drift(team):
    current = compute_team_hash(team.agents)
    if SNAPSHOT_PATH.exists():
        saved = json.loads(SNAPSHOT_PATH.read_text())["hash"]
        if saved != current:
            raise SystemExit(
                f"Team config changed ({saved[:8]} → {current[:8]}). "
                "Re-baseline your eval suite before merging."
            )
    SNAPSHOT_PATH.write_text(json.dumps({"hash": current}))
```

### In eval workflows — gate regression checks

```python
from antcrew.eval import EvalSuite
from antcrew.core.agent import compute_team_hash

team   = DevTeam(model=llm)
suite  = EvalSuite.load("suites/core.json")

# Verify config matches the baseline's governance hash
baseline = EvalSuite.load_reports("baselines/core.json")
# (store hash alongside baseline JSON for strict matching)

current  = suite.run(team)
ok, diff = suite.regression_check(baseline, current)
```

---

## API reference

### `BaseAgent.stage: str`

Class-level pipeline stage label. Defaults to `""` (no stage). Set on the class, not in `__init__`:

```python
class MyAgent(BaseAgent):
    stage = "planning"
```

### `BaseAgent.governance_hash: str`

Read-only property. 16-char SHA-256 prefix over `name | role_description | stage | system_prompt_suffix | sorted(tool.name for tool in tools)`.

### `compute_team_hash(agents: list[BaseAgent]) -> str`

Module-level function from `antcrew.core.agent`. Returns a 16-char SHA-256 over the sorted set of `{agent.name}:{agent.governance_hash}` pairs.

```python
from antcrew.core.agent import compute_team_hash
# or
from antcrew import compute_team_hash
```
