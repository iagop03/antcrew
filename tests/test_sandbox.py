"""Tests for SandboxRunner / LocalRunner."""
from __future__ import annotations

import pytest

from antcrew.core.artifacts import CodeArtifact, TestArtifact
from antcrew.sandbox.runner import LocalRunner, RunResult, SandboxRunner, _parse_counts


# ---------------------------------------------------------------------------
# Helpers — build minimal artifacts with valid content
# ---------------------------------------------------------------------------

def _test_art(content: str, path: str = "tests/test_x.py") -> TestArtifact:
    return TestArtifact(
        ticket_id="T-1",
        file_path=path,
        description="test",
        language="python",
        content=content,
        coverage_areas=["unit"],
    )


def _code_art(content: str, path: str = "mymod.py") -> CodeArtifact:
    return CodeArtifact(
        ticket_id="T-1",
        file_path=path,
        description="module",
        language="python",
        content=content,
    )


# ---------------------------------------------------------------------------
# _parse_counts — unit tests (no subprocess)
# ---------------------------------------------------------------------------

def test_parse_all_pass():
    assert _parse_counts("5 passed in 0.12s") == (5, 0, 0)

def test_parse_mixed():
    assert _parse_counts("3 passed, 2 failed in 1.23s") == (3, 2, 0)

def test_parse_errors():
    assert _parse_counts("1 error in 0.05s") == (0, 0, 1)

def test_parse_errors_plural():
    assert _parse_counts("2 errors in 0.05s") == (0, 0, 2)

def test_parse_all_fail():
    assert _parse_counts("4 failed in 0.80s") == (0, 4, 0)

def test_parse_no_tests():
    assert _parse_counts("no tests ran") == (0, 0, 0)

def test_parse_with_warnings():
    # Warnings appear in summary but should not affect counts
    assert _parse_counts("5 passed, 2 warnings in 0.30s") == (5, 0, 0)


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------

def test_run_result_success_all_pass():
    r = RunResult(passed=3, failed=0, errors=0, total=3,
                  duration_ms=100.0, output="", returncode=0)
    assert r.success is True

def test_run_result_success_false_when_failed():
    r = RunResult(passed=2, failed=1, errors=0, total=3,
                  duration_ms=100.0, output="", returncode=1)
    assert r.success is False

def test_run_result_success_false_when_error():
    r = RunResult(passed=0, failed=0, errors=1, total=1,
                  duration_ms=0.0, output="", returncode=2)
    assert r.success is False

def test_run_result_summary_passed_only():
    r = RunResult(passed=3, failed=0, errors=0, total=3,
                  duration_ms=412.0, output="", returncode=0)
    assert r.summary() == "3 passed in 412ms"

def test_run_result_summary_mixed():
    r = RunResult(passed=2, failed=1, errors=0, total=3,
                  duration_ms=120.0, output="", returncode=1)
    assert "2 passed" in r.summary()
    assert "1 failed" in r.summary()

def test_run_result_summary_no_tests():
    r = RunResult(passed=0, failed=0, errors=0, total=0,
                  duration_ms=0.0, output="", returncode=0)
    assert r.summary() == "no tests ran in 0ms"

def test_run_result_to_dict_keys():
    r = RunResult(passed=1, failed=0, errors=0, total=1,
                  duration_ms=50.0, output="ok", returncode=0)
    d = r.to_dict()
    assert d["passed"]   == 1
    assert d["success"]  is True
    assert d["output"]   == "ok"
    assert "duration_ms" in d


# ---------------------------------------------------------------------------
# SandboxRunner ABC
# ---------------------------------------------------------------------------

def test_sandbox_runner_is_abstract():
    with pytest.raises(TypeError):
        SandboxRunner()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# LocalRunner — subprocess tests (real pytest execution)
# ---------------------------------------------------------------------------

def test_passing_tests():
    runner = LocalRunner()
    art = _test_art("def test_pass(): assert 1 + 1 == 2\n")
    result = runner.run([art])
    assert result.passed == 1
    assert result.failed == 0
    assert result.errors == 0
    assert result.success is True
    assert result.returncode == 0

def test_failing_tests():
    runner = LocalRunner()
    art = _test_art("def test_fail(): assert 1 == 2\n")
    result = runner.run([art])
    assert result.failed == 1
    assert result.success is False

def test_mixed_pass_fail():
    runner = LocalRunner()
    art = _test_art(
        "def test_ok(): assert True\n"
        "def test_bad(): assert False\n"
    )
    result = runner.run([art])
    assert result.passed == 1
    assert result.failed == 1

def test_multiple_test_files():
    runner = LocalRunner()
    arts = [
        _test_art("def test_a(): assert True\n", "tests/test_a.py"),
        _test_art("def test_b(): assert True\n", "tests/test_b.py"),
    ]
    result = runner.run(arts)
    assert result.passed == 2
    assert result.failed == 0

def test_with_code_artifact():
    """Test file imports a code artifact — LocalRunner must write both."""
    runner = LocalRunner()
    code = _code_art("def add(a, b): return a + b\n", "mathmod.py")
    test = _test_art(
        "from mathmod import add\n"
        "def test_add(): assert add(1, 2) == 3\n",
        "tests/test_mathmod.py",
    )
    result = runner.run([test], code_artifacts=[code])
    assert result.passed == 1
    assert result.errors == 0

def test_code_in_subdir():
    """Code artifact in a sub-package is importable."""
    runner = LocalRunner()
    init = _code_art("", "src/__init__.py")
    code = _code_art("def greet(): return 'hello'\n", "src/greet.py")
    test = _test_art(
        "from src.greet import greet\n"
        "def test_greet(): assert greet() == 'hello'\n",
        "tests/test_greet.py",
    )
    result = runner.run([test], code_artifacts=[init, code])
    assert result.passed == 1

def test_import_error_counted_as_error():
    """A test file that fails to import increments errors."""
    runner = LocalRunner()
    art = _test_art(
        "from nonexistent_module_xyz import something\n"
        "def test_x(): assert True\n"
    )
    result = runner.run([art])
    assert result.errors >= 1
    assert result.success is False

def test_syntax_error_counted_as_error():
    """A test file with a syntax error increments errors."""
    runner = LocalRunner()
    art = _test_art("def test_x(\n")  # broken syntax
    result = runner.run([art])
    assert result.errors >= 1
    assert result.success is False

def test_no_artifacts_returns_zero():
    runner = LocalRunner()
    result = runner.run([])
    assert result.total == 0
    assert result.success is True
    assert result.returncode == 0

def test_output_captured():
    runner = LocalRunner()
    art = _test_art("def test_ok(): assert True\n")
    result = runner.run([art])
    assert result.output  # pytest produces some output
    assert "passed" in result.output.lower() or result.passed >= 0

def test_duration_positive():
    runner = LocalRunner()
    art = _test_art("def test_ok(): assert True\n")
    result = runner.run([art])
    assert result.duration_ms > 0

def test_total_equals_sum():
    runner = LocalRunner()
    art = _test_art(
        "def test_ok(): assert True\n"
        "def test_bad(): assert False\n"
    )
    result = runner.run([art])
    assert result.total == result.passed + result.failed + result.errors


# ---------------------------------------------------------------------------
# LocalRunner timeout (short synthetic timeout)
# ---------------------------------------------------------------------------

def test_timeout_returns_error():
    runner = LocalRunner(timeout=1)  # 1 second — very tight
    # A test that sleeps longer than the timeout
    art = _test_art(
        "import time\n"
        "def test_slow(): time.sleep(10)\n"
    )
    result = runner.run([art])
    # Either timed out or the test was collected/errored; either way not success
    assert result.success is False


# ---------------------------------------------------------------------------
# Integration with DevTeam
# ---------------------------------------------------------------------------

def test_dev_team_with_runner_populates_test_results():
    """DevTeam with a QA node runs generated tests via the injected runner."""
    from antcrew.agents.qa import QAAgent
    from antcrew.core.supervisor import Supervisor
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    runner = LocalRunner()
    # DevTeam defaults to BA→PM→BackendDev; extend it to include QA
    team = DevTeam(
        model=SimulatedLLM(),
        runner=runner,
        agents={"qa": QAAgent(SimulatedLLM())},
        supervisor=Supervisor(flow=[
            ("business_analyst", "pm"),
            ("pm", "backend_dev"),
            ("backend_dev", "qa"),
        ]),
    )
    state = team.run("Build a login module")

    assert state.get("test_results") is not None
    result = state["test_results"]
    assert isinstance(result, RunResult)
    assert result.total >= 0

def test_dev_team_without_runner_has_no_test_results():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.dev_team import DevTeam

    team = DevTeam(model=SimulatedLLM())
    state = team.run("Build a login module")
    # test_results stays None when no runner is injected
    assert state.get("test_results") is None

def test_fullstack_team_with_runner():
    from antcrew.models.simulated import SimulatedLLM
    from antcrew.teams.fullstack_team import FullStackTeam

    runner = LocalRunner()
    team = FullStackTeam(model=SimulatedLLM(), runner=runner)
    state = team.run("Build a todo app")
    assert state.get("test_results") is not None


# ---------------------------------------------------------------------------
# Top-level import
# ---------------------------------------------------------------------------

def test_importable_from_antcrew():
    from antcrew import LocalRunner as LR, RunResult as RR, SandboxRunner as SR
    assert LR is LocalRunner
    assert RR is RunResult
    assert SR is SandboxRunner
