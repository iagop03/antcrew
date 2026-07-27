"""Tests for `antcrew publish` CLI command."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from antcrew.cli import app

cli = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_state_file(tmp_path: Path, *, with_prd=True, with_code=True) -> Path:
    state: dict = {}
    if with_prd:
        state["prd"] = {
            "title": "Auth Module",
            "summary": "JWT auth system",
            "goals": ["Secure login"],
            "functional_requirements": ["Login endpoint"],
            "out_of_scope": [],
            "non_functional_requirements": [],
            "open_questions": [],
        }
    if with_code:
        state["code_artifacts"] = [
            {
                "ticket_id": "T-1",
                "file_path": "src/auth.py",
                "description": "JWT auth module",
                "content": "def login(): pass",
            }
        ]
    f = tmp_path / "state.json"
    f.write_text(json.dumps(state), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

def test_publish_no_flags_exits_cleanly(tmp_path):
    """Without --github or --confluence, command exits 0 with a helpful message."""
    f = _make_state_file(tmp_path)
    result = cli.invoke(app, ["publish", str(f)])
    assert result.exit_code == 0
    assert "Nothing to publish" in result.output


def test_publish_state_file_not_found(tmp_path):
    result = cli.invoke(app, ["publish", str(tmp_path / "missing.json"), "--github"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_publish_github_requires_token(tmp_path):
    f = _make_state_file(tmp_path)
    result = cli.invoke(app, ["publish", str(f), "--github", "--repo", "org/repo"])
    assert result.exit_code == 1
    assert "GITHUB_TOKEN" in result.output or "token" in result.output.lower()


def test_publish_github_requires_repo(tmp_path):
    f = _make_state_file(tmp_path)
    result = cli.invoke(app, ["publish", str(f), "--github", "--token", "gh-tok"])
    assert result.exit_code == 1
    assert "repo" in result.output.lower()


def test_publish_confluence_requires_url(tmp_path):
    f = _make_state_file(tmp_path)
    result = cli.invoke(app, [
        "publish", str(f), "--confluence",
        "--confluence-email", "a@b.com",
        "--confluence-token", "tok",
        "--space", "DEV",
        # missing --confluence-url
    ])
    assert result.exit_code == 1
    assert "confluence-url" in result.output.lower() or "missing" in result.output.lower()


def test_publish_confluence_requires_space(tmp_path):
    f = _make_state_file(tmp_path)
    result = cli.invoke(app, [
        "publish", str(f), "--confluence",
        "--confluence-url", "https://org.atlassian.net",
        "--confluence-email", "a@b.com",
        "--confluence-token", "tok",
        # missing --space
    ])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# GitHub happy path (mocked)
# ---------------------------------------------------------------------------

def test_publish_github_opens_pr(tmp_path):
    f = _make_state_file(tmp_path)
    with patch("antcrew.integrations.github.GitHubIntegration.create_pr") as mock_pr:
        mock_pr.return_value = "https://github.com/org/repo/pull/42"
        result = cli.invoke(app, [
            "publish", str(f),
            "--github",
            "--token", "gh-tok",
            "--repo", "org/repo",
        ])
    assert result.exit_code == 0, result.output
    assert "https://github.com/org/repo/pull/42" in result.output
    mock_pr.assert_called_once()


def test_publish_github_error_shows_message(tmp_path):
    f = _make_state_file(tmp_path)
    with patch("antcrew.integrations.github.GitHubIntegration.create_pr") as mock_pr:
        mock_pr.side_effect = RuntimeError("API rate limit exceeded")
        result = cli.invoke(app, [
            "publish", str(f),
            "--github",
            "--token", "gh-tok",
            "--repo", "org/repo",
        ])
    assert result.exit_code == 1
    assert "API rate limit exceeded" in result.output


# ---------------------------------------------------------------------------
# Confluence happy path (mocked)
# ---------------------------------------------------------------------------

def test_publish_confluence_publishes_prd(tmp_path):
    f = _make_state_file(tmp_path)
    mock_page = {"id": "123", "title": "PRD — Auth Module"}
    with patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_prd") as mock_prd, \
         patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_research") as mock_res, \
         patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_docs") as mock_docs:
        mock_prd.return_value = mock_page
        mock_res.return_value = None
        mock_docs.return_value = []
        result = cli.invoke(app, [
            "publish", str(f),
            "--confluence",
            "--confluence-url", "https://org.atlassian.net",
            "--confluence-email", "a@b.com",
            "--confluence-token", "tok",
            "--space", "DEV",
        ])
    assert result.exit_code == 0, result.output
    assert "PRD" in result.output
    mock_prd.assert_called_once()


def test_publish_confluence_nothing_to_publish(tmp_path):
    """State with no PRD, research, or docs shows the 'Nothing to publish' warning."""
    f = tmp_path / "empty.json"
    f.write_text(json.dumps({}), encoding="utf-8")
    with patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_prd") as mock_prd, \
         patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_research") as mock_res, \
         patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_docs") as mock_docs:
        mock_prd.return_value = None
        mock_res.return_value = None
        mock_docs.return_value = []
        result = cli.invoke(app, [
            "publish", str(f),
            "--confluence",
            "--confluence-url", "https://org.atlassian.net",
            "--confluence-email", "a@b.com",
            "--confluence-token", "tok",
            "--space", "DEV",
        ])
    assert result.exit_code == 0
    assert "Nothing" in result.output


# ---------------------------------------------------------------------------
# State hydration
# ---------------------------------------------------------------------------

def test_publish_hydrates_prd_from_dict(tmp_path):
    """State file PRD dict is re-hydrated to a Pydantic PRD before integrations see it."""
    f = _make_state_file(tmp_path)
    captured = {}

    def _fake_create_pr(state, **kwargs):
        captured["prd"] = state.get("prd")
        return "https://github.com/org/repo/pull/1"

    with patch("antcrew.integrations.github.GitHubIntegration.create_pr", side_effect=_fake_create_pr):
        cli.invoke(app, [
            "publish", str(f),
            "--github",
            "--token", "gh-tok",
            "--repo", "org/repo",
        ])

    from antcrew.core.artifacts import PRD
    assert isinstance(captured.get("prd"), PRD)
    assert captured["prd"].title == "Auth Module"


def test_publish_both_github_and_confluence(tmp_path):
    """--github and --confluence can be combined in a single invocation."""
    f = _make_state_file(tmp_path)
    with patch("antcrew.integrations.github.GitHubIntegration.create_pr") as mock_gh, \
         patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_prd") as mock_cf_prd, \
         patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_research") as mock_cf_res, \
         patch("antcrew.integrations.confluence.ConfluenceIntegration.publish_docs") as mock_cf_docs:
        mock_gh.return_value = "https://github.com/org/repo/pull/99"
        mock_cf_prd.return_value = {"id": "1", "title": "PRD — Auth Module"}
        mock_cf_res.return_value = None
        mock_cf_docs.return_value = []
        result = cli.invoke(app, [
            "publish", str(f),
            "--github", "--token", "gh-tok", "--repo", "org/repo",
            "--confluence",
            "--confluence-url", "https://org.atlassian.net",
            "--confluence-email", "a@b.com",
            "--confluence-token", "tok",
            "--space", "DEV",
        ])
    assert result.exit_code == 0, result.output
    mock_gh.assert_called_once()
    mock_cf_prd.assert_called_once()
