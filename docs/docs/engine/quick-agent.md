# Quick start — inline agents

`QuickAgent` and the `antcrew quick` CLI command let you define and run a multi-agent pipeline with zero Python code. Role and goal are specified as strings — no class definition, typed artifacts, or EventBus knowledge required.

This is the fastest path from idea to running pipeline: role and goal as strings, typed artifact contracts and TraceLog underneath.

## CLI

```bash
antcrew quick "Research AI agent frameworks" \
  "Researcher: Find and compare the top Python AI agent frameworks released in 2025" \
  "Analyst: Identify the 3 most significant differences from a production perspective" \
  "Writer: Write a clear 300-word summary for a technical decision-maker"
```

Each argument after the goal is an agent spec: `"Role: description of what this agent does"`. Agents run sequentially; each sees the previous agent's output.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `claude` | LLM to use (same as `antcrew run`) |
| `--json` | `false` | Print full state JSON instead of the result panel |
| `--push URL` | — | Dispatch to a remote platform instance |
| `--api-key KEY` | `$ANTCREW_API_KEY` | Platform API key (required with `--push`) |

### Push to platform

```bash
antcrew quick "Build a login system" \
  "Architect: Design the auth architecture" \
  "Developer: Write the implementation plan" \
  --push https://antcrew.org \
  --api-key acw_...
```

## Python API

```python
from antcrew.agents.quick_agent import QuickAgent, QuickTeam
from antcrew.models.anthropic_model import AnthropicModel

llm = AnthropicModel()

# Single agent
agent = QuickAgent(llm, role="Researcher", goal="Find recent papers on RAG")
state = agent.run({"request": "What's new in vector retrieval?"})
print(state["result"])

# Multi-agent team
team = QuickTeam(
    specs=[
        "Researcher: Find and summarize recent papers on RAG",
        "Writer: Synthesize findings into a clear report",
    ],
    llm=llm,
)
result = team.run("What's new in vector retrieval?")
print(result["result"])
```

## How it compares to BaseAgent

| Feature | QuickAgent | BaseAgent subclass |
|---------|------------|-------------------|
| Setup time | Seconds (one string) | Minutes (class + imports) |
| Typed artifacts | No (dict passthrough) | Yes (Pydantic contracts) |
| Governance hash | Yes | Yes |
| HITL | Not built-in | Built-in (`approval_required`) |
| Memory | Yes (`_mem_get`/`_mem_set`) | Yes |
| Production recommended | Prototyping | Yes |

## Migrating to typed agents

When a QuickAgent proves valuable and needs typed outputs or HITL:

```python
# Start with QuickAgent:
specs = ["Researcher: Find papers on RAG"]

# Graduate to typed agent:
from antcrew.core.agent import BaseAgent
from antcrew.core.artifacts import ArtifactContract, ResearchDocument

research_contract = ArtifactContract("research_document", ResearchDocument)

class ResearchAgent(BaseAgent):
    name = "researcher"
    role_description = "Find papers on RAG"
    consumes = ["request"]
    produces = ["research_document"]

    def run(self, state):
        result = self.system("Find papers on RAG.", state["request"])
        doc = ResearchDocument(title="RAG Research", key_findings=[result])
        return research_contract.inject(doc)
```
