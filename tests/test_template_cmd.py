"""Tests for `antcrew template` CLI commands (P2.2)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client(responses: list[tuple[int, object]]):
    """Return a context-manager mock httpx.Client that returns fixed responses."""
    mock_responses = []
    for status, body in responses:
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = body
        resp.raise_for_status = MagicMock(
            side_effect=None if status < 400 else Exception(f"HTTP {status}")
        )
        mock_responses.append(resp)

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get = MagicMock(side_effect=mock_responses[:])
    client.post = MagicMock(side_effect=mock_responses[:])
    client.delete = MagicMock(side_effect=mock_responses[:])
    return client


_TEMPLATES = [
    {"id": 1, "name": "backend-sprint", "team": "DevTeam",
     "request": "Build user auth module", "hitl": False,
     "max_cost_usd": None, "repo_url": None, "created_at": "2026-01-01T00:00:00"},
    {"id": 2, "name": "full-app", "team": "FullStackTeam",
     "request": "Build e-commerce storefront", "hitl": True,
     "max_cost_usd": 5.0, "repo_url": None, "created_at": "2026-01-02T00:00:00"},
]


# ---------------------------------------------------------------------------
# template list
# ---------------------------------------------------------------------------

def test_template_list_empty(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    client = _mock_client([(200, [])])
    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, ["template", "list"])
    assert result.exit_code == 0
    assert "No templates" in result.output


def test_template_list_shows_templates(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    client = _mock_client([(200, _TEMPLATES)])
    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, ["template", "list"])
    assert result.exit_code == 0
    assert "backend-sprint" in result.output
    assert "DevTeam" in result.output
    assert "full-app" in result.output
    assert "hitl" in result.output.lower()


def test_template_list_http_error(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.get.side_effect = Exception("connection refused")
    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, ["template", "list"])
    assert result.exit_code == 1
    assert "Error" in result.output


# ---------------------------------------------------------------------------
# template save
# ---------------------------------------------------------------------------

def test_template_save_success(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    created = {"id": 3, "name": "my-template", "team": "DevTeam",
               "request": "Write tests", "hitl": False}
    client = _mock_client([(201, created)])
    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, [
            "template", "save",
            "--name", "my-template",
            "--team", "DevTeam",
            "--request", "Write tests",
        ])
    assert result.exit_code == 0
    assert "Saved" in result.output
    assert "my-template" in result.output
    assert "antcrew template run 3" in result.output


def test_template_save_with_hitl_and_cost(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    created = {"id": 4, "name": "hitl-run", "team": "FullStackTeam",
               "request": "Build API", "hitl": True, "max_cost_usd": 2.5}

    captured = {}

    def _post(path, **kwargs):
        captured.update(kwargs.get("json", {}))
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = created
        resp.raise_for_status = MagicMock()
        return resp

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post = _post

    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, [
            "template", "save",
            "--name", "hitl-run",
            "--team", "FullStackTeam",
            "--request", "Build API",
            "--hitl",
            "--max-cost", "2.5",
        ])

    assert result.exit_code == 0
    assert captured["hitl"] is True
    assert captured["max_cost_usd"] == 2.5


# ---------------------------------------------------------------------------
# template delete
# ---------------------------------------------------------------------------

def test_template_delete_with_yes_flag(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    resp = MagicMock()
    resp.status_code = 204
    resp.raise_for_status = MagicMock()
    client.delete = MagicMock(return_value=resp)

    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, ["template", "delete", "1", "--yes"])

    assert result.exit_code == 0
    assert "Deleted" in result.output
    client.delete.assert_called_once_with("/templates/1")


# ---------------------------------------------------------------------------
# template run (dry-run only — avoids spinning up LLM)
# ---------------------------------------------------------------------------

def test_template_run_dry_run(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    client = _mock_client([(200, _TEMPLATES)])
    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, ["template", "run", "1", "--dry-run"])
    assert result.exit_code == 0
    assert "backend-sprint" in result.output
    assert "dry-run" in result.output.lower()


def test_template_run_not_found(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    client = _mock_client([(200, _TEMPLATES)])
    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, ["template", "run", "999", "--dry-run"])
    assert result.exit_code == 1
    assert "999" in result.output


def test_template_run_request_override(monkeypatch):
    monkeypatch.setenv("ANTCREW_PLATFORM_URL", "http://localhost:8000")
    monkeypatch.setenv("ANTCREW_PLATFORM_API_KEY", "sk-test")
    client = _mock_client([(200, _TEMPLATES)])
    with patch("httpx.Client", return_value=client):
        result = runner.invoke(app, [
            "template", "run", "1",
            "--request", "Do something different",
            "--dry-run",
        ])
    assert result.exit_code == 0
    assert "Do something different" in result.output


# ---------------------------------------------------------------------------
# team name mapping
# ---------------------------------------------------------------------------

def test_platform_to_oss_mapping():
    from antcrew.cli.template_cmd import _PLATFORM_TO_OSS
    assert _PLATFORM_TO_OSS["DevTeam"] == "dev"
    assert _PLATFORM_TO_OSS["FullStackTeam"] == "fullstack"
    assert _PLATFORM_TO_OSS["ResearchTeam"] == "research"
    assert _PLATFORM_TO_OSS["ContentTeam"] == "content"
    assert _PLATFORM_TO_OSS["FeatureTeam"] == "feature"
