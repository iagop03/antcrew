# Engine SDK

The engine is the autonomous execution loop built into antcrew. It drives an `EngineLoop` over a set of capabilities until a goal is satisfied — no fixed pipeline, no manual step sequencing.

```bash
pip install antcrew   # antcrew_engine is bundled — no separate install needed
```

> **Note:** `antcrew-engine` was a separate package until antcrew v0.35.0. It is now merged into antcrew. All imports (`from antcrew_engine import EngineLoop`) continue to work unchanged.

---

## What it provides

**EngineLoop** — the decision loop. On each iteration it inspects the current project state, selects the best-fit capability (Architect, CodeGenerator, TestRunner…), dispatches it, and checks whether the goal conditions are now satisfied.

**Built-in capabilities** — Architect, TaskPlanner, CodeGenerator, TestGenerator, TestRunner, BugFixer, CodeReviewer, DocGenerator, SecurityScanner, and more. Each capability reads from the `ArtifactStore` and writes typed, Pydantic-validated artifacts back to it.

**[Documentation Module](documentation-module.md)** — index your project's existing docs (markdown, Word, PDF, Jira, COBOL) and inject relevant context into every capability's LLM prompt via `--schema` / `--docs-dir` on the CLI, or `DocumentationManager` in the Python API.

**[Legacy / COBOL Support](legacy-cobol.md)** — first-class support for IBM AS/400 organisations: parse `.cbl`/`.cpy` files, connect to DB2 for i via `AS400Connector`, add AI to COBOL programs with `COBOLAugment`, or translate programs to Python/Java/Go with `polytranslate`.

**Provider-agnostic model calls** — change `"claude:claude-sonnet-5"` to `"openai:gpt-4o"` and nothing else changes. `build_llm()` resolves the string to the correct provider client.

**EventLog** — every capability dispatch, result, and retry is written to a structured, append-only event log. antcrew-platform receives these events in real time and shows them in the Runs dashboard.

**HITL checkpoints** — `HitlReviewer` is a built-in capability that pauses the loop and sends a review request to antcrew-platform. Execution resumes once a human approves or rejects from the dashboard.

---

## Quick start

```python
from antcrew_engine import (
    EngineLoop, MemoryStore, Goal, DesiredProjectState,
    Constraints, Condition, ConditionId,
    Architect, TaskPlanner, CodeGenerator, TestGenerator, TestRunner,
    artifact_validators,
)
from antcrew_engine import CapabilityRegistry
from antcrew_engine.config import build_llm
from antcrew_engine.engine import EventLog

# 1. Configure the LLM
llm = build_llm("claude:claude-sonnet-5")

# 2. Register capabilities
registry = CapabilityRegistry()
registry.register(Architect(llm))
registry.register(TaskPlanner(llm))
registry.register(CodeGenerator(llm))
registry.register(TestGenerator(llm))
registry.register(TestRunner())

# 3. Define the goal
goal = Goal(
    description="Build a JWT authentication module",
    desired_state=DesiredProjectState(
        conditions=[
            Condition(ConditionId("architecture_exists"), "Architecture document produced"),
            Condition(ConditionId("implementation_exists"), "All tasks have code files"),
            Condition(ConditionId("tests_pass"), "Test suite passes"),
        ]
    ),
    constraints=Constraints(max_iterations=20),
)

# 4. Run
store = MemoryStore()
event_log = EventLog()
engine = EngineLoop(registry, artifact_validators, event_log)
final_state = engine.run(store, goal)
```

---

## Connecting to the platform

To stream events to antcrew-platform in real time, pass a `PlatformEventBridge` as the event log:

```python
from antcrew_engine.engine import EventBusBridge

bridge = EventBusBridge(
    platform_url="https://antcrew.org",
    api_key="acw_live_...",
    run_id="your-run-id",
)
engine = EngineLoop(registry, artifact_validators, bridge)
```

Every capability dispatch and result will appear in the platform dashboard under the run's event timeline.

---

## Core concepts

| Concept | What it is |
|---|---|
| `EngineLoop` | The decision loop — selects and dispatches capabilities until the goal is satisfied |
| `Capability` | A discrete unit of work (Architect, CodeGenerator, TestRunner…) that reads and writes typed artifacts |
| `ArtifactStore` | In-memory (`MemoryStore`) or filesystem (`FilesystemStore`) store for typed artifacts |
| `Goal` | The target state the engine works toward, expressed as `Condition` objects |
| `EventLog` / `EventBusBridge` | Structured log of every engine event; bridged to the platform for live observability |
| `build_llm(model)` | Factory that resolves a model string to a provider-specific LLM client |

[:octicons-arrow-right-24: Typed artifacts](contracts.md)

[:octicons-arrow-right-24: EventLog & Replay](tracelog.md)

[:octicons-arrow-right-24: LLM providers](providers.md)
