"""Tests for `antcrew configure` command (P1.3 — global cascade)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from antcrew.cli import app
from antcrew.cli.configure_cmd import apply_config_to_env, load_config

runner = CliRunner()


# ---------------------------------------------------------------------------
# load_config — cascade
# ---------------------------------------------------------------------------

def test_load_config_returns_empty_when_no_files(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg == {}


def test_load_config_reads_project_file(tmp_path):
    cfg_dir = tmp_path / ".antcrew"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        yaml.dump({"platform_url": "http://local", "api_key": "sk-proj"}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg["platform_url"] == "http://local"
    assert cfg["api_key"] == "sk-proj"


def test_load_config_reads_global_file(tmp_path, monkeypatch):
    global_dir = tmp_path / "home" / ".antcrew"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(
        yaml.dump({"platform_url": "https://global.example.com", "api_key": "sk-global"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    cfg = load_config(project_dir)
    assert cfg["platform_url"] == "https://global.example.com"


def test_load_config_project_overrides_global(tmp_path, monkeypatch):
    global_dir = tmp_path / "home" / ".antcrew"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(
        yaml.dump({"platform_url": "https://global.example.com", "api_key": "sk-global"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    project_dir = tmp_path / "project"
    (project_dir / ".antcrew").mkdir(parents=True)
    (project_dir / ".antcrew" / "config.yaml").write_text(
        yaml.dump({"api_key": "sk-project-override"}),
        encoding="utf-8",
    )

    cfg = load_config(project_dir)
    assert cfg["platform_url"] == "https://global.example.com"   # from global
    assert cfg["api_key"] == "sk-project-override"               # project wins


# ---------------------------------------------------------------------------
# apply_config_to_env — injects into os.environ
# ---------------------------------------------------------------------------

def test_apply_config_injects_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / ".antcrew").mkdir()
    (tmp_path / ".antcrew" / "config.yaml").write_text(
        yaml.dump({"platform_url": "http://test-inject", "api_key": "sk-inject"}),
        encoding="utf-8",
    )
    monkeypatch.delenv("ANTCREW_PLATFORM_URL", raising=False)
    monkeypatch.delenv("ANTCREW_PLATFORM_API_KEY", raising=False)

    apply_config_to_env(tmp_path)

    assert os.environ["ANTCREW_PLATFORM_URL"] == "http://test-inject"
    assert os.environ["ANTCREW_PLATFORM_API_KEY"] == "sk-inject"


def test_apply_config_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / ".antcrew").mkdir()
    (tmp_path / ".antcrew" / "config.yaml").write_text(
        yaml.dump({"platform_url": "http://from-file"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://already-set")

    apply_config_to_env(tmp_path)

    assert os.environ["ANTCREW_PLATFORM_URL"] == "http://already-set"


# ---------------------------------------------------------------------------
# antcrew configure — write project-level (default)
# ---------------------------------------------------------------------------

def test_configure_writes_project_config(tmp_path):
    result = runner.invoke(
        app,
        ["configure", "--platform-url", "http://my-platform", "--api-key", "sk-abc123",
         "--output", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    cfg_path = tmp_path / ".antcrew" / "config.yaml"
    assert cfg_path.exists()
    data = yaml.safe_load(cfg_path.read_text())
    assert data["platform_url"] == "http://my-platform"
    assert data["api_key"] == "sk-abc123"


def test_configure_writes_global_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    result = runner.invoke(
        app,
        ["configure", "--global", "--platform-url", "https://global.example.com",
         "--api-key", "sk-global"],
    )
    assert result.exit_code == 0, result.output
    global_path = tmp_path / "home" / ".antcrew" / "config.yaml"
    assert global_path.exists()
    data = yaml.safe_load(global_path.read_text())
    assert data["platform_url"] == "https://global.example.com"
    assert data["api_key"] == "sk-global"
    assert "global" in result.output


def test_configure_merges_into_existing(tmp_path):
    cfg_dir = tmp_path / ".antcrew"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        yaml.dump({"platform_url": "http://old", "api_key": "sk-old"}),
        encoding="utf-8",
    )
    runner.invoke(
        app,
        ["configure", "--api-key", "sk-new", "--output", str(tmp_path)],
    )
    data = yaml.safe_load((cfg_dir / "config.yaml").read_text())
    assert data["platform_url"] == "http://old"   # unchanged
    assert data["api_key"] == "sk-new"             # updated


def test_configure_strips_trailing_slash_from_url(tmp_path):
    runner.invoke(
        app,
        ["configure", "--platform-url", "http://my-platform/", "--output", str(tmp_path)],
    )
    data = yaml.safe_load((tmp_path / ".antcrew" / "config.yaml").read_text())
    assert data["platform_url"] == "http://my-platform"


def test_configure_exits_1_with_no_args(tmp_path):
    result = runner.invoke(app, ["configure", "--output", str(tmp_path)])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# antcrew configure --show
# ---------------------------------------------------------------------------

def test_configure_show_prints_project(tmp_path):
    cfg_dir = tmp_path / ".antcrew"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        yaml.dump({"platform_url": "http://show-test", "api_key": "sk-abcdefgh"}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["configure", "--show", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "http://show-test" in result.output
    assert "sk-a" in result.output       # masked: first 4 chars visible
    assert "efgh" in result.output       # masked: last 4 chars visible
    assert "sk-abcdefgh" not in result.output   # full key must NOT appear


def test_configure_show_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    result = runner.invoke(app, ["configure", "--show", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "No config" in result.output
