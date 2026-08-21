"""CodeMigrationTeam — automated code modernisation and migration pipeline.

Four-agent pipeline:
  1. CodebaseScanner   — maps the codebase, finds migration candidates
  2. MigrationPlanner  — creates a migration plan with file-by-file strategy
  3. CodeMigrator      — applies migrations to each file
  4. MigrationVerifier — validates the output, identifies remaining issues

Usage::

    from antcrew import CodeMigrationTeam
    from antcrew.models.anthropic_model import AnthropicModel

    team = CodeMigrationTeam(
        llm=AnthropicModel(),
        source_pattern="Python 2",
        target_pattern="Python 3.11",
    )
    result = team.run("Migrate all Python 2 code in src/ to Python 3.11")
    plan = result.get("migration_plan")  # MigrationPlanArtifact dict

Common migration types:
  - Python 2 → Python 3
  - SQLAlchemy 1.x → 2.x
  - Django 3.x → 4.x / 5.x
  - CommonJS → ESM
  - Class components → React hooks
  - Monolith → microservices (identify bounded contexts)
"""
from __future__ import annotations

import logging
from typing import Optional

from antcrew.core.supervisor import Supervisor
from antcrew.models.base import BaseLLM

log = logging.getLogger(__name__)

_SCANNER_PROMPT = """\
You are a codebase analysis expert. Scan the provided codebase description and
identify all files and patterns that require migration from {source_pattern}
to {target_pattern}.

For each file/pattern found, output:
FILE: <path> | PATTERN: <what needs to change> | COMPLEXITY: low|medium|high

Also output:
TOTAL_FILES: <count>
MIGRATION_SCOPE: <1-2 sentence summary of the migration scope>
"""

_PLANNER_PROMPT = """\
You are a migration architect. Based on the scan results below, create a
detailed migration plan from {source_pattern} to {target_pattern}.

Scan results:
{scan_results}

For each file, specify:
1. Migration strategy (automated | semi-automated | manual review required)
2. Dependencies to update first
3. Breaking changes to watch for
4. Test strategy

Output format:
MIGRATION ORDER:
1. <file_path> — <strategy> — <reason>

BREAKING CHANGES:
- <description>

DEPENDENCY UPDATES:
- <package>: <old_version> → <new_version>
"""

_MIGRATOR_PROMPT = """\
You are an expert code migration engineer specialising in {source_pattern} to
{target_pattern} migrations.

Migration plan:
{migration_plan_raw}

For each file in the migration order:
1. Show the key patterns that need changing
2. Provide the migrated code snippet or transformation rule
3. Mark as AUTO_FIX if the change can be applied with a simple find/replace or
   codemod, otherwise MANUAL_REVIEW

Format:
FILE: <path>
STATUS: AUTO_FIX | MANUAL_REVIEW
TRANSFORMATION: <description or code>
---
"""

_VERIFIER_PROMPT = """\
You are a migration quality assurance engineer. Review the migration output
below and identify any remaining issues or incomplete migrations.

Migration output:
{migration_output}

Check for:
- Patterns that were missed
- Incorrect transformations
- Backwards incompatibilities introduced
- Missing test coverage for migrated code

Output:
REMAINING_ISSUES:
- FILE: <path> | SEVERITY: <critical|warning|info> | ISSUE: <description>

VERIFICATION_SUMMARY: <overall assessment>
TEST_RECOMMENDATION: <what tests to add/update>
MIGRATION_COMPLETE: yes | no | partial
"""


class CodeMigrationTeam:
    """Multi-agent pipeline for codebase migration and modernisation.

    Args:
        llm: LLM instance for all agents.
        source_pattern: What is being migrated FROM (e.g. "Python 2", "SQLAlchemy 1.x").
        target_pattern: What is being migrated TO (e.g. "Python 3.11", "SQLAlchemy 2.x").
        project_dirs: Optional list of directories to scan for migration candidates.
    """

    def __init__(
        self,
        llm: Optional[BaseLLM] = None,
        *,
        source_pattern: str = "",
        target_pattern: str = "",
        project_dirs: Optional[list[str]] = None,
    ) -> None:
        if llm is None:
            from antcrew.models.anthropic_model import AnthropicModel
            llm = AnthropicModel()

        self._llm = llm
        self._source_pattern = source_pattern
        self._target_pattern = target_pattern
        self._project_dirs = project_dirs or []

        src = source_pattern or "legacy code"
        tgt = target_pattern or "modern equivalent"

        # Substitute only {source_pattern}/{target_pattern} — leave {input_key} placeholders intact
        def _sub(tmpl: str) -> str:
            return tmpl.replace("{source_pattern}", src).replace("{target_pattern}", tgt)

        from antcrew.agents.template_agent import TemplateAgent
        self._scanner = TemplateAgent(
            {"name": "migration_scanner", "system_prompt": _sub(_SCANNER_PROMPT),
             "output_key": "scan_results"},
            llm,
        )
        self._planner = TemplateAgent(
            {"name": "migration_planner", "system_prompt": _sub(_PLANNER_PROMPT),
             "input_key": "scan_results", "output_key": "migration_plan_raw"},
            llm,
        )
        self._migrator = TemplateAgent(
            {"name": "code_migrator", "system_prompt": _sub(_MIGRATOR_PROMPT),
             "input_key": "migration_plan_raw", "output_key": "migration_output"},
            llm,
        )
        self._verifier = TemplateAgent(
            {"name": "migration_verifier", "system_prompt": _VERIFIER_PROMPT,
             "input_key": "migration_output", "output_key": "verification_result"},
            llm,
        )

    @property
    def _agents(self) -> list:
        return [self._scanner, self._planner, self._migrator, self._verifier]

    def run(self, request: str) -> dict:
        """Run the migration pipeline.

        Args:
            request: Natural language description of the migration task,
                e.g. "Migrate all Python 2 print statements in src/ to Python 3".

        Returns:
            State dict including ``migration_plan`` (MigrationPlanArtifact dict).
        """
        from antcrew.core.events import new_run_id
        supervisor = Supervisor(flow=[
            ("migration_scanner", "migration_planner"),
            ("migration_planner", "code_migrator"),
            ("code_migrator", "migration_verifier"),
        ])
        graph = supervisor.build({
            "migration_scanner": self._scanner,
            "migration_planner": self._planner,
            "code_migrator": self._migrator,
            "migration_verifier": self._verifier,
        })
        _run_id = new_run_id()
        thread_id = _run_id

        context = request
        if self._project_dirs:
            context += f"\n\nProject directories to scan: {', '.join(self._project_dirs)}"

        state = {"request": context, "_run_id": _run_id, "_thread_id": thread_id}
        config = {"configurable": {"thread_id": thread_id}}
        result = graph.invoke(state, config=config)
        plan = self._parse_plan(result)
        result["migration_plan"] = plan.model_dump()
        log.info(
            "migration: %d issues, %d auto-fixable, complete=%s",
            len(plan.issues),
            plan.auto_fixable_count,
            plan.test_passed,
        )
        return result

    def _parse_plan(self, result: dict) -> "MigrationPlanArtifact":
        from antcrew.core.artifacts import MigrationIssue, MigrationPlanArtifact

        scan_raw = result.get("scan_results", "")
        verif_raw = result.get("verification_result", "")

        total_files = 0
        for line in scan_raw.splitlines():
            if line.strip().upper().startswith("TOTAL_FILES:"):
                try:
                    total_files = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

        issues: list[MigrationIssue] = []
        for line in verif_raw.splitlines():
            line = line.strip()
            if not (line.startswith("- FILE:") or line.startswith("FILE:")):
                continue
            try:
                parts = {p.split(":", 1)[0].strip().upper(): p.split(":", 1)[1].strip()
                         for p in line.lstrip("- ").split("|") if ":" in p}
                severity_raw = parts.get("SEVERITY", "warning").lower()
                severity = severity_raw if severity_raw in ("critical", "warning", "info") else "warning"
                issues.append(MigrationIssue(
                    file_path=parts.get("FILE", ""),
                    description=parts.get("ISSUE", ""),
                    severity=severity,  # type: ignore[arg-type]
                    auto_fixable="auto" in parts.get("ISSUE", "").lower(),
                ))
            except Exception:
                continue

        complete_str = ""
        for line in verif_raw.splitlines():
            if "MIGRATION_COMPLETE" in line.upper():
                complete_str = line.split(":", 1)[-1].strip().lower()
                break

        summary = ""
        for line in verif_raw.splitlines():
            if "VERIFICATION_SUMMARY" in line.upper():
                summary = line.split(":", 1)[-1].strip()
                break

        return MigrationPlanArtifact(
            source_pattern=self._source_pattern,
            target_pattern=self._target_pattern,
            total_files=total_files,
            issues=issues,
            test_passed=complete_str == "yes",
            summary=summary or verif_raw[:300],
        )
