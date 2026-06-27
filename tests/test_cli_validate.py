"""Tests for `antcrew validate` command (v0.8.9)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
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
