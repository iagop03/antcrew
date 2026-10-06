# Agent memory between runs

Each team in AntCrew has a persistent key-value memory scoped to its workspace. Agents read and write arbitrary keys during a run; the platform persists those values so the next run starts with the team's prior context.

## How it works

1. Before a run starts, the platform loads the team's memory from the `run_memory` table
2. Each agent in the team receives an `InMemoryKVMemory` instance pre-populated with that data
3. Agents use `self._mem_get(key)` and `self._mem_set(key, value)` during `run()`
4. After the run completes, the platform saves the updated snapshot back to the database

## Using memory in agents

```python
from antcrew.core.agent import BaseAgent
from antcrew.core.state import TeamState

class ResearchAgent(BaseAgent):
    name = "researcher"
    role_description = "Analyzes papers and tracks findings across runs."
    consumes = ["request"]
    produces = ["research_document"]

    def run(self, state: TeamState) -> dict:
        # Read what was found in previous runs
        prior_findings = self._mem_get("prior_findings") or []
        
        new_findings = self.system(
            "You are a researcher. Here are prior findings from past runs: "
            + str(prior_findings),
            state["request"],
        )
        
        # Save for next run — appends to the list
        all_findings = prior_findings + [new_findings[:200]]
        self._mem_set("prior_findings", all_findings[-10:])  # keep last 10
        
        return {"research_document": new_findings, "current_agent": self.name}
```

!!! note
    Memory is shared across all agents in the same team. Use namespaced keys (`researcher:findings`, `writer:style_notes`) when multiple agents write to memory to avoid key collisions.

## Platform API

Inspect and edit team memory directly:

```bash
# Read
curl -H "X-Api-Key: acw_..." https://antcrew.org/memory/DevTeam

# Write / update
curl -X PUT -H "X-Api-Key: acw_..." \
     -H "Content-Type: application/json" \
     -d '{"data": {"last_sprint": "S23", "velocity": 42}}' \
     https://antcrew.org/memory/DevTeam

# Delete a key
curl -X DELETE -H "X-Api-Key: acw_..." \
     https://antcrew.org/memory/DevTeam/last_sprint

# Clear all memory
curl -X DELETE -H "X-Api-Key: acw_..." \
     https://antcrew.org/memory/DevTeam
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/memory/{team}` | GET | Full memory dict |
| `/memory/{team}` | PUT | Merge-upsert keys (body: `{"data": {...}}`) |
| `/memory/{team}/{key}` | DELETE | Remove one key |
| `/memory/{team}` | DELETE | Clear all memory |

## When memory is not wired

`self._mem_get()` returns `None` and `self._mem_set()` is a no-op when:

- Running locally without the platform (unit tests, CLI without `--push`)
- `workspace_id` is not set for the run

Agents must handle `None` returns from `_mem_get` gracefully — which is always correct since the first run has no prior state.
