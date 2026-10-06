# Code Migration Pipeline

`CodeMigrationTeam` is a four-agent pipeline for automated code modernisation. It scans a codebase, plans a migration strategy, applies transformations, and verifies the result — producing a structured `MigrationPlanArtifact`.

## Agents

| Agent | Role |
|---|---|
| `migration_scanner` | Maps files and patterns that need migration |
| `migration_planner` | Creates a file-by-file migration strategy with dependency ordering |
| `code_migrator` | Applies transformations; marks AUTO_FIX vs MANUAL_REVIEW |
| `migration_verifier` | Checks for remaining issues, incomplete migrations, missing tests |

## Common migration types

- Python 2 → Python 3.11+
- SQLAlchemy 1.x → 2.x (async ORM)
- Django 3.x → 5.x
- CommonJS → ESM (Node.js)
- Class components → React hooks
- Monolith → microservices (bounded context analysis)
- OpenAI SDK v0 → v1

## Quickstart

```python
from antcrew import CodeMigrationTeam
from antcrew.models.anthropic_model import AnthropicModel

team = CodeMigrationTeam(
    llm=AnthropicModel(),
    source_pattern="Python 2",
    target_pattern="Python 3.11",
    project_dirs=["src/", "scripts/"],
)

result = team.run(
    "Migrate all Python 2 syntax in src/ to Python 3.11. "
    "Focus on print statements, unicode strings, integer division, and urllib."
)

plan = result["migration_plan"]   # dict (MigrationPlanArtifact)

print(f"Total files: {plan['total_files']}")
print(f"Issues found: {len(plan['issues'])}")
print(f"Auto-fixable: {plan['auto_fixable_count']}")
print(f"Tests passed: {plan['test_passed']}")
```

## MigrationPlanArtifact

```python
from antcrew import MigrationPlanArtifact, MigrationIssue

plan = MigrationPlanArtifact(
    source_pattern="Python 2",
    target_pattern="Python 3.11",
    total_files=47,
    issues=[
        MigrationIssue(
            file_path="src/utils/compat.py",
            line=23,
            issue_type="print_statement",
            description="print statement must become print() call",
            suggested_fix="print(...)",
            severity="warning",
            auto_fixable=True,
        ),
        MigrationIssue(
            file_path="src/db/queries.py",
            issue_type="unicode_literal",
            description="u'...' prefix is valid in Python 3 but signals legacy code",
            severity="info",
            auto_fixable=True,
        ),
    ],
    test_passed=True,
    summary="47 files scanned. 31 auto-fixable issues. 2 require manual review.",
)
```

### Fields

| Field | Type | Description |
|---|---|---|
| `source_pattern` | `str` | What is being migrated FROM |
| `target_pattern` | `str` | What is being migrated TO |
| `total_files` | `int` | Number of files scanned |
| `issues` | `list[MigrationIssue]` | All identified migration issues |
| `migrated_files` | `list[str]` | Files where migration was applied |
| `skipped_files` | `list[str]` | Files skipped (binary, generated, etc.) |
| `test_passed` | `bool` | Whether the verifier confirmed migration complete |
| `summary` | `str` | Verifier's overall assessment |
| `critical_count` | `int` (property) | Issues with severity = critical |
| `auto_fixable_count` | `int` (property) | Issues the verifier marked AUTO_FIX |

## Integration with SandboxRunner

After the migration planner produces transformed files, validate them with the sandbox:

```python
from antcrew import CodeMigrationTeam, SandboxRunResult
from antcrew.sandbox import LocalRunner

team = CodeMigrationTeam(llm=AnthropicModel(), source_pattern="Python 2", target_pattern="Python 3.11")
result = team.run("Migrate src/")

# Run tests on migrated output
runner = LocalRunner(timeout=120)
run_result = runner.run(result.get("test_artifacts", []))
print(f"Tests: {run_result.passed} passed, {run_result.failed} failed")
```

## Prompt regression gate

After migration, use `antcrew regtest` to verify that agent outputs didn't regress:

```bash
antcrew regtest baseline.db \
  --run $BASELINE_RUN_ID \
  --agent migration_planner \
  --prompt prompts/migrator_v2.txt \
  --threshold 0.15
```

## YAML (CustomTeam)

```yaml
team: custom
model: claude
steps:
  - name: migration_scanner
    system_prompt: |
      Scan the codebase described below and identify all files and patterns
      that need migration from Python 2 to Python 3.11.
      Output: FILE: <path> | PATTERN: <what to change> | COMPLEXITY: low|medium|high
      Also output: TOTAL_FILES: <n>
    output_key: scan_results

  - name: migration_planner
    system_prompt: |
      Based on {scan_results}, create a migration order (dependencies first),
      list breaking changes, and specify which dependency versions to update.
    input_key: scan_results
    output_key: migration_plan_raw

  - name: code_migrator
    system_prompt: |
      For each file in {migration_plan_raw}, show the transformation.
      Mark AUTO_FIX if a codemod can handle it, MANUAL_REVIEW otherwise.
    input_key: migration_plan_raw
    output_key: migration_output

  - name: migration_verifier
    system_prompt: |
      Review {migration_output}. List remaining issues as:
      FILE: <path> | SEVERITY: <critical|warning|info> | ISSUE: <desc>
      End with MIGRATION_COMPLETE: yes | no | partial
    input_key: migration_output
    output_key: verification_result
```
