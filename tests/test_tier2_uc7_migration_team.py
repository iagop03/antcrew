"""Tests for UC7: CodeMigrationTeam + MigrationPlanArtifact."""
from __future__ import annotations

import pytest
from antcrew.core.artifacts import MigrationIssue, MigrationPlanArtifact


# ---------------------------------------------------------------------------
# MigrationPlanArtifact
# ---------------------------------------------------------------------------

def test_migration_plan_defaults():
    p = MigrationPlanArtifact()
    assert p.source_pattern == ""
    assert p.target_pattern == ""
    assert p.total_files == 0
    assert p.issues == []
    assert p.migrated_files == []
    assert p.skipped_files == []
    assert p.test_passed is False
    assert p.summary == ""


def test_migration_plan_critical_count_property():
    p = MigrationPlanArtifact(issues=[
        MigrationIssue(file_path="a.py", description="x", severity="critical"),
        MigrationIssue(file_path="b.py", description="y", severity="warning"),
        MigrationIssue(file_path="c.py", description="z", severity="critical"),
    ])
    assert p.critical_count == 2


def test_migration_plan_auto_fixable_count():
    p = MigrationPlanArtifact(issues=[
        MigrationIssue(file_path="a.py", description="x", auto_fixable=True),
        MigrationIssue(file_path="b.py", description="y", auto_fixable=False),
        MigrationIssue(file_path="c.py", description="z", auto_fixable=True),
    ])
    assert p.auto_fixable_count == 2


def test_migration_plan_no_issues():
    p = MigrationPlanArtifact()
    assert p.critical_count == 0
    assert p.auto_fixable_count == 0


def test_migration_plan_serializes():
    p = MigrationPlanArtifact(
        source_pattern="Python 2",
        target_pattern="Python 3.11",
        total_files=10,
        test_passed=True,
        summary="Migration complete.",
    )
    d = p.model_dump()
    assert d["source_pattern"] == "Python 2"
    assert d["total_files"] == 10
    assert d["test_passed"] is True


def test_migration_plan_with_migrated_files():
    p = MigrationPlanArtifact(
        migrated_files=["src/a.py", "src/b.py"],
        skipped_files=["vendor/old.py"],
    )
    assert len(p.migrated_files) == 2
    assert len(p.skipped_files) == 1


# ---------------------------------------------------------------------------
# MigrationIssue
# ---------------------------------------------------------------------------

def test_migration_issue_defaults():
    i = MigrationIssue(file_path="a.py", description="needs change")
    assert i.line is None
    assert i.issue_type == ""
    assert i.suggested_fix == ""
    assert i.severity == "warning"
    assert i.auto_fixable is False


def test_migration_issue_critical_severity():
    i = MigrationIssue(file_path="a.py", description="x", severity="critical")
    assert i.severity == "critical"


def test_migration_issue_auto_fixable():
    i = MigrationIssue(file_path="a.py", description="print statement", auto_fixable=True)
    assert i.auto_fixable is True


def test_migration_issue_with_line():
    i = MigrationIssue(file_path="a.py", description="x", line=42)
    assert i.line == 42


def test_migration_issue_invalid_severity():
    """Pydantic should reject invalid severity literals."""
    with pytest.raises(Exception):  # ValidationError
        MigrationIssue(file_path="a.py", description="x", severity="unknown")


# ---------------------------------------------------------------------------
# CodeMigrationTeam — instantiation (no LLM calls)
# ---------------------------------------------------------------------------

def test_code_migration_team_instantiates():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(
        llm=SimulatedLLM(),
        source_pattern="Python 2",
        target_pattern="Python 3.11",
    )
    assert team._source_pattern == "Python 2"
    assert team._target_pattern == "Python 3.11"


def test_code_migration_team_has_four_agents():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(llm=SimulatedLLM())
    assert len(team._agents) == 4


def test_code_migration_team_agent_names():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(llm=SimulatedLLM())
    names = [a.name for a in team._agents]
    assert "migration_scanner" in names
    assert "migration_planner" in names
    assert "code_migrator" in names
    assert "migration_verifier" in names


def test_code_migration_team_project_dirs():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(llm=SimulatedLLM(), project_dirs=["src/", "tests/"])
    assert "src/" in team._project_dirs


def test_code_migration_team_importable_from_antcrew():
    from antcrew import CodeMigrationTeam, MigrationPlanArtifact, MigrationIssue  # noqa: F401
    assert CodeMigrationTeam is not None


def test_code_migration_team_simulated_run():
    """SimulatedLLM returns fake output — pipeline should not crash."""
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(
        llm=SimulatedLLM(),
        source_pattern="Python 2",
        target_pattern="Python 3.11",
    )
    result = team.run("Migrate print statements in src/")
    assert "migration_plan" in result
    plan = result["migration_plan"]
    assert isinstance(plan, dict)
    assert "issues" in plan
    assert "test_passed" in plan
    assert "source_pattern" in plan


def test_code_migration_team_parse_plan_total_files():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(llm=SimulatedLLM())
    result = {
        "scan_results": "FILE: a.py | PATTERN: print | COMPLEXITY: low\nTOTAL_FILES: 7",
        "verification_result": "MIGRATION_COMPLETE: yes\nVERIFICATION_SUMMARY: Clean.",
    }
    plan = team._parse_plan(result)
    assert plan.total_files == 7
    assert plan.test_passed is True
    assert "Clean" in plan.summary


def test_code_migration_team_parse_plan_issues():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(llm=SimulatedLLM())
    result = {
        "scan_results": "TOTAL_FILES: 3",
        "verification_result": (
            "REMAINING_ISSUES:\n"
            "- FILE: a.py | SEVERITY: critical | ISSUE: raw_input not replaced\n"
            "- FILE: b.py | SEVERITY: warning | ISSUE: urllib.urlopen deprecated\n"
            "MIGRATION_COMPLETE: partial\n"
            "VERIFICATION_SUMMARY: Two issues remain.\n"
        ),
    }
    plan = team._parse_plan(result)
    assert len(plan.issues) == 2
    assert plan.issues[0].severity == "critical"
    assert plan.test_passed is False


def test_code_migration_team_no_project_dirs_defaults():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.migration_team import CodeMigrationTeam
    team = CodeMigrationTeam(llm=SimulatedLLM())
    assert team._project_dirs == []
