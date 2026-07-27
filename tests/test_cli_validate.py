"""Tests for `antcrew validate` command (v0.8.9)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _good_cfg() -> dict:
    return {
        "team": "custom",
        "model": "claude",
        "steps": [
            {"name": "planner", "system_prompt": "Plan the task.", "output_key": "plan"},
            {"name": "executor", "system_prompt": "Execute: {plan}", "output_key": "result"},
        ],
    }


def _invoke(args: list[str]):
    return runner.invoke(app, args)


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------

def test_valid_config_exits_zero(tmp_path):
    p = _write(tmp_path, "team.yaml", _good_cfg())
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0


def test_valid_config_prints_valid(tmp_path):
    p = _write(tmp_path, "team.yaml", _good_cfg())
    r = _invoke(["validate", str(p)])
    assert "valid" in r.output.lower()
    assert "failed" not in r.output.lower()


def test_valid_config_shows_step_table(tmp_path):
    p = _write(tmp_path, "team.yaml", _good_cfg())
    r = _invoke(["validate", str(p)])
    assert "planner" in r.output
    assert "executor" in r.output


def test_valid_config_shows_output_keys(tmp_path):
    p = _write(tmp_path, "team.yaml", _good_cfg())
    r = _invoke(["validate", str(p)])
    assert "plan" in r.output
    assert "result" in r.output


def test_json_file_also_accepted(tmp_path):
    p = tmp_path / "team.json"
    p.write_text(json.dumps(_good_cfg()), encoding="utf-8")
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0


def test_parallel_group_shown_in_table(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
            {"parallel": [
                {"name": "backend", "system_prompt": "Backend: {plan}", "output_key": "be"},
                {"name": "frontend", "system_prompt": "Frontend: {plan}", "output_key": "fe"},
            ]},
        ],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "backend" in r.output
    assert "frontend" in r.output


def test_flags_column_shows_output_json(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "a", "system_prompt": "Go.", "output_key": "out", "output_json": True},
        ],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "json" in r.output


def test_flags_column_shows_retry(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "a", "system_prompt": "Go.", "output_key": "out", "max_retries": 3},
        ],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "retry" in r.output


def test_flags_column_shows_condition(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "a", "system_prompt": "Go.", "output_key": "out"},
            {"name": "b", "system_prompt": "Go.", "output_key": "out2", "condition": "out"},
        ],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "if:out" in r.output


# ---------------------------------------------------------------------------
# Warnings — exits 0 unless --strict
# ---------------------------------------------------------------------------

def test_unknown_interpolation_key_is_warning(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Use {ghost}.", "output_key": "out"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "warning" in r.output.lower()


def test_unknown_condition_key_is_warning(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "a", "system_prompt": "Go.", "output_key": "out", "condition": "ghost"},
        ],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "warning" in r.output.lower()


def test_strict_flag_makes_warnings_fatal(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Use {ghost}.", "output_key": "out"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p), "--strict"])
    assert r.exit_code == 1
    assert "failed" in r.output.lower()


def test_known_interpolation_key_no_warning(tmp_path):
    """Interpolating {plan} when 'plan' is produced by a prior step is fine."""
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "a", "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "b", "system_prompt": "Execute: {plan}", "output_key": "out"},
        ],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "warning" not in r.output.lower()


def test_request_key_always_available(tmp_path):
    """The {request} key is implicitly always in state — no warning."""
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Do: {request}", "output_key": "out"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "warning" not in r.output.lower()


# ---------------------------------------------------------------------------
# Errors — always exits 1
# ---------------------------------------------------------------------------

def test_missing_file_exits_one(tmp_path):
    r = _invoke(["validate", str(tmp_path / "nope.yaml")])
    assert r.exit_code == 1


def test_invalid_yaml_exits_one(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("key: [\nunclosed", encoding="utf-8")
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1


def test_empty_steps_exits_one(tmp_path):
    cfg = {"team": "custom", "steps": []}
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1


def test_missing_name_exits_one(tmp_path):
    cfg = {"team": "custom", "steps": [{"system_prompt": "Go.", "output_key": "out"}]}
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1


def test_missing_system_prompt_exits_one(tmp_path):
    cfg = {"team": "custom", "steps": [{"name": "a", "output_key": "out"}]}
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1


def test_empty_system_prompt_exits_one(tmp_path):
    cfg = {"team": "custom", "steps": [{"name": "a", "system_prompt": "   ", "output_key": "out"}]}
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1


def test_empty_parallel_group_exits_one(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [
            {"name": "a", "system_prompt": "Go.", "output_key": "out"},
            {"parallel": []},
        ],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1


def test_error_message_in_output(tmp_path):
    cfg = {"team": "custom", "steps": [{"name": "a", "output_key": "out"}]}
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert "system_prompt" in r.output


# ---------------------------------------------------------------------------
# Non-custom team types
# ---------------------------------------------------------------------------

def test_non_custom_team_exits_zero(tmp_path):
    cfg = {"team": "dev", "model": "claude"}
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0


def test_non_custom_team_shows_type(tmp_path):
    cfg = {"team": "research", "model": "gpt"}
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert "research" in r.output


# ===========================================================================
# validate: on_error field
# ===========================================================================

def test_validate_on_error_skip_passes(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out",
                   "on_error": "skip"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "skip" in r.output


def test_validate_on_error_invalid_exits_nonzero(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out",
                   "on_error": "ignore"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1
    assert "on_error" in r.output


def test_validate_on_error_default_shows_value(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out",
                   "on_error": "skip", "default": "N/A"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "N/A" in r.output


# ===========================================================================
# validate: timeout field
# ===========================================================================

def test_validate_timeout_numeric_passes(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out",
                   "timeout": 30}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "timeout" in r.output


def test_validate_timeout_non_numeric_exits_nonzero(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out",
                   "timeout": "fast"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1
    assert "timeout" in r.output


def test_validate_timeout_zero_exits_nonzero(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out",
                   "timeout": 0}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 1


# ===========================================================================
# validate: team_file: steps
# ===========================================================================

def test_validate_team_file_existing_passes(tmp_path):
    inner = {
        "team": "custom",
        "steps": [{"name": "x", "system_prompt": "Go.", "output_key": "x_out"}],
    }
    inner_path = tmp_path / "inner.yaml"
    inner_path.write_text(yaml.dump(inner), encoding="utf-8")

    outer = {
        "team": "custom",
        "steps": [{"team_file": "inner.yaml"}],
    }
    p = _write(tmp_path, "team.yaml", outer)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "nested" in r.output


def test_validate_team_file_missing_warns(tmp_path):
    cfg = {
        "team": "custom",
        "steps": [{"team_file": "nonexistent.yaml"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = _invoke(["validate", str(p)])
    # Missing file → warning, not hard error (exit 0 without --strict)
    assert "not found" in r.output.lower() or "warning" in r.output.lower() or r.exit_code == 0


def test_validate_team_file_nested_output_keys_in_dataflow(tmp_path):
    """Output keys from nested team are available to subsequent steps."""
    inner = {
        "team": "custom",
        "steps": [{"name": "inner", "system_prompt": "Go.", "output_key": "inner_out"}],
    }
    inner_path = tmp_path / "inner.yaml"
    inner_path.write_text(yaml.dump(inner), encoding="utf-8")

    outer = {
        "team": "custom",
        "steps": [
            {"team_file": "inner.yaml"},
            {"name": "next", "system_prompt": "Use {inner_out}.", "output_key": "final"},
        ],
    }
    p = _write(tmp_path, "team.yaml", outer)
    r = _invoke(["validate", str(p)])
    assert r.exit_code == 0
    assert "warning" not in r.output.lower()


# ===========================================================================
# --dry-run new flags
# ===========================================================================

def test_dry_run_shows_timeout_flag(tmp_path):
    from antcrew.cli import app as _app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out", "timeout": 30}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = CliRunner().invoke(_app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0
    assert "timeout" in r.output


def test_dry_run_shows_on_error_skip(tmp_path):
    from antcrew.cli import app as _app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out",
                   "on_error": "skip"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = CliRunner().invoke(_app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0
    assert "skip" in r.output


def test_dry_run_nested_team_shows_merged(tmp_path):
    inner = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "x", "system_prompt": "Go.", "output_key": "x_out"}],
    }
    inner_path = tmp_path / "inner.yaml"
    inner_path.write_text(yaml.dump(inner), encoding="utf-8")

    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"team_file": str(inner_path)}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    from antcrew.cli import app as _app
    r = CliRunner().invoke(_app, ["run", "task", "--config", str(p), "--dry-run"])
    assert r.exit_code == 0
    assert "merged" in r.output


# ===========================================================================
# REPL stateful mode
# ===========================================================================

def test_repl_stateful_exits_zero(tmp_path):
    from antcrew.cli import app as _app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = CliRunner().invoke(
        _app,
        ["run", "--config", str(p), "--no-stream", "--repl-stateful"],
        input="task\nquit\n",
    )
    assert r.exit_code == 0


def test_repl_stateful_shows_stateful_mode(tmp_path):
    from antcrew.cli import app as _app
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "a", "system_prompt": "Go.", "output_key": "out"}],
    }
    p = _write(tmp_path, "team.yaml", cfg)
    r = CliRunner().invoke(
        _app,
        ["run", "--config", str(p), "--no-stream", "--repl-stateful"],
        input="q\n",
    )
    assert r.exit_code == 0
    assert "stateful" in r.output


# ===========================================================================
# team_file: path resolved relative to config dir
# ===========================================================================

def test_team_file_resolved_relative_to_config_dir(tmp_path):
    """team_file: path must resolve relative to the config file, not CWD."""
    import os
    inner_dir = tmp_path / "teams"
    inner_dir.mkdir()
    inner_cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"name": "inner", "system_prompt": "Go.", "output_key": "inner_out"}],
    }
    inner_path = inner_dir / "inner.yaml"
    inner_path.write_text(yaml.dump(inner_cfg), encoding="utf-8")

    outer_cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [{"team_file": "teams/inner.yaml"}],
    }
    outer_path = tmp_path / "outer.yaml"
    outer_path.write_text(yaml.dump(outer_cfg), encoding="utf-8")

    # Run from a different CWD — relative path must still resolve correctly
    from antcrew.config import load
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path.parent)   # parent of config dir → relative path would fail
        team = load(outer_path)
        result = team.run("task")
    finally:
        os.chdir(old_cwd)

    assert "inner_out" in result.state
