# Typed artifacts

Every capability in antcrew-engine reads and writes **typed artifacts** — Pydantic models stored in the `ArtifactStore`. Typing is enforced at the capability boundary: a capability declares the artifact kinds it produces, and the engine's validators confirm the output conforms to the expected schema before marking a condition satisfied.

## How it works

```
Capability.run(store, goal, llm)
  → calls LLM with a structured prompt
  → parses response into a Pydantic model
  → writes typed Artifact to ArtifactStore
  → EngineLoop checks validators → condition satisfied or retry
```

## Built-in artifact kinds

| `ArtifactKind` | Produced by | Schema |
|---|---|---|
| `architecture` | `Architect` | `ArchitectureDoc` (components, decisions, tech stack) |
| `task_graph` | `TaskPlanner` | `TaskGraph` (tasks with deps and acceptance criteria) |
| `code_file` | `CodeGenerator` | `CodeFile` (path, content, language) |
| `test_file` | `TestGenerator` | `TestFile` (path, content, test framework) |
| `test_result` | `TestRunner` | `TestResult` (passed, failed, output) |
| `review` | `CodeReviewer` | `ReviewResult` (verdict, issues, suggestions) |
| `doc_file` | `DocGenerator` | `DocFile` (path, content, format) |
| `spec` | `SpecExtractor` | `Spec` (requirements, constraints, acceptance criteria) |

## Reading artifacts from the store

```python
from antcrew_engine import MemoryStore, ArtifactKind

store = MemoryStore()

# After engine.run() completes:
arch = store.get(ArtifactKind.architecture)          # ArchitectureDoc | None
code_files = store.get_all(ArtifactKind.code_file)  # list[CodeFile]
test_result = store.get(ArtifactKind.test_result)   # TestResult | None
```

## Writing custom capabilities

To add domain-specific work to the engine, subclass `BaseExecutor`:

```python
from antcrew_engine.capabilities.base import BaseExecutor
from antcrew_engine.engine import CapabilityResult, ArtifactStore
from antcrew_engine.engine.goal import Goal
from pydantic import BaseModel

class DatabaseSchema(BaseModel):
    tables: list[str]
    relationships: list[str]

class SchemaDesigner(BaseExecutor):
    name = "SchemaDesigner"
    produces = ["db_schema"]
    requires = ["architecture"]

    def run(self, store: ArtifactStore, goal: Goal, llm) -> CapabilityResult:
        arch = store.get("architecture")
        schema = self._call_llm(llm, arch, output_model=DatabaseSchema)
        store.put("db_schema", schema)
        return CapabilityResult(produced=["db_schema"])
```

Register it alongside the built-in capabilities:

```python
registry.register(SchemaDesigner(llm))
```

## Validators

Validators inspect the current store state and determine which `Condition` objects are satisfied. Each built-in artifact kind has a corresponding validator in `antcrew_engine.capabilities.validators`. Pass `artifact_validators` (the complete default set) to `EngineLoop` unless you are overriding specific conditions.

```python
from antcrew_engine import artifact_validators
from antcrew_engine.engine import EngineLoop

engine = EngineLoop(registry, artifact_validators, event_log)
```

To add a validator for a custom artifact:

```python
from antcrew_engine.engine.validator import Validator
from antcrew_engine.engine.goal import ConditionId

class DbSchemaValidator(Validator):
    condition_id = ConditionId("db_schema_exists")

    def check(self, store) -> bool:
        return store.get("db_schema") is not None

engine = EngineLoop(registry, artifact_validators + [DbSchemaValidator()], event_log)
```
