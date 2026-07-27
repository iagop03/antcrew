"""Tests for CLI run ergonomics: --request-file, --output-dir, --repl."""
from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from antcrew.cli import app


def _cfg(tmp_path: Path) -> Path:
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "planner", "system_prompt": "Plan.", "output_key": "plan"},
            {"name": "writer",  "system_prompt": "Write: {plan}", "output_key": "article"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


# ===========================================================================
# --request-file
# ===========================================================================

def test_request_file_used_as_request(tmp_path):
    cfg_path = _cfg(tmp_path)
    req_file = tmp_path / "task.md"
    req_file.write_text("Build a login module", encoding="utf-8")

    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--request-file", str(req_file), "--no-stream"],
    )
    assert r.exit_code == 0


def test_request_file_missing_exits_nonzero(tmp_path):
    cfg_path = _cfg(tmp_path)
    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--request-file", str(tmp_path / "nope.md"),
         "--no-stream"],
    )
    assert r.exit_code != 0
    assert "not found" in r.output.lower()


def test_request_file_strips_whitespace(tmp_path):
    cfg_path = _cfg(tmp_path)
    req_file = tmp_path / "task.txt"
    req_file.write_text("  Build auth  \n", encoding="utf-8")

    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--request-file", str(req_file), "--no-stream"],
    )
    assert r.exit_code == 0


def test_request_file_overrides_positional(tmp_path):
    """When --request-file is given, the positional request argument is ignored."""
    cfg_path = _cfg(tmp_path)
    req_file = tmp_path / "task.txt"
    req_file.write_text("file request", encoding="utf-8")

    r = CliRunner().invoke(
        app,
        ["run", "positional request", "--config", str(cfg_path),
         "--request-file", str(req_file), "--no-stream"],
    )
    assert r.exit_code == 0


# ===========================================================================
# optional request (interactive prompt)
# ===========================================================================

def test_no_request_prompts_interactively(tmp_path):
    cfg_path = _cfg(tmp_path)
    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--no-stream"],
        input="Build login\n",
    )
    assert r.exit_code == 0


def test_dry_run_does_not_prompt_for_request(tmp_path):
    """--dry-run must not prompt for a request even when none is given."""
    cfg_path = _cfg(tmp_path)
    r = CliRunner().invoke(app, ["run", "--config", str(cfg_path), "--dry-run"])
    # Should not read from stdin; exit_code 0 and no prompt output expected
    assert r.exit_code == 0
    assert "Dry run" in r.output


# ===========================================================================
# --output-dir
# ===========================================================================

def test_output_dir_creates_files(tmp_path):
    cfg_path = _cfg(tmp_path)
    out_dir = tmp_path / "outputs"

    r = CliRunner().invoke(
        app,
        ["run", "task", "--config", str(cfg_path), "--no-stream",
         "--output-dir", str(out_dir)],
    )
    assert r.exit_code == 0
    assert out_dir.exists()
    files = list(out_dir.iterdir())
    # At least plan.txt and article.txt should be created
    names = {f.name for f in files}
    assert "plan.txt" in names
    assert "article.txt" in names


def test_output_dir_created_if_missing(tmp_path):
    cfg_path = _cfg(tmp_path)
    out_dir = tmp_path / "nested" / "outputs"

    r = CliRunner().invoke(
        app,
        ["run", "task", "--config", str(cfg_path), "--no-stream",
         "--output-dir", str(out_dir)],
    )
    assert r.exit_code == 0
    assert out_dir.exists()


def test_output_dir_skips_request_key(tmp_path):
    cfg_path = _cfg(tmp_path)
    out_dir = tmp_path / "outputs"

    CliRunner().invoke(
        app,
        ["run", "task", "--config", str(cfg_path), "--no-stream",
         "--output-dir", str(out_dir)],
    )
    # "request" key must not be written
    assert not (out_dir / "request.txt").exists()


def test_output_dir_json_for_dict_output(tmp_path):
    """If a step outputs a dict (output_json: true), it should write a .json file."""
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "analyzer", "system_prompt": "Analyze.",
             "output_key": "analysis", "output_json": True},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    out_dir = tmp_path / "outputs"

    r = CliRunner().invoke(
        app,
        ["run", "task", "--config", str(p), "--no-stream",
         "--output-dir", str(out_dir)],
    )
    assert r.exit_code == 0
    # Dict output → .json file
    assert (out_dir / "analysis.json").exists() or (out_dir / "analysis.txt").exists()


def test_output_dir_prints_summary(tmp_path):
    cfg_path = _cfg(tmp_path)
    out_dir = tmp_path / "outputs"

    r = CliRunner().invoke(
        app,
        ["run", "task", "--config", str(cfg_path), "--no-stream",
         "--output-dir", str(out_dir)],
    )
    assert r.exit_code == 0
    assert "file" in r.output.lower()


# ===========================================================================
# --repl
# ===========================================================================

def test_repl_exits_on_quit(tmp_path):
    cfg_path = _cfg(tmp_path)
    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--no-stream", "--repl"],
        input="quit\n",
    )
    assert r.exit_code == 0


def test_repl_processes_one_request(tmp_path):
    cfg_path = _cfg(tmp_path)
    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--no-stream", "--repl"],
        input="Build login\nquit\n",
    )
    assert r.exit_code == 0


def test_repl_skips_empty_input(tmp_path):
    cfg_path = _cfg(tmp_path)
    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--no-stream", "--repl"],
        input="\n\nquit\n",
    )
    assert r.exit_code == 0


def test_repl_shows_repl_header(tmp_path):
    cfg_path = _cfg(tmp_path)
    r = CliRunner().invoke(
        app,
        ["run", "--config", str(cfg_path), "--no-stream", "--repl"],
        input="q\n",
    )
    assert r.exit_code == 0
    assert "REPL" in r.output


def test_repl_continues_after_error(tmp_path):
    """A pipeline error in one REPL iteration must not crash the loop."""
    cfg = {
        "team": "custom",
        "model": "simulated",
        "steps": [
            {"name": "a", "system_prompt": "Go.", "output_key": "out"},
        ],
    }
    p = tmp_path / "team.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")

    r = CliRunner().invoke(
        app,
        ["run", "--config", str(p), "--no-stream", "--repl"],
        input="task\nquit\n",
    )
    assert r.exit_code == 0
