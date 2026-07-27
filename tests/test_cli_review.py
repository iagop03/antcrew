"""Tests for antcrew review command."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from antcrew.cli import app

runner = CliRunner()

_REVIEW = {
    "review_id": "rev-abc-000",
    "run_id": "run-xyz-111",
    "agent_name": "PM Agent",
    "artifact_json": json.dumps({"title": "Auth PRD", "tickets": [{"title": "Ticket 1"}]}),
    "options_json": json.dumps(["approve", "reject"]),
    "status": "pending",
}


def _mock_get(reviews: list) -> MagicMock:
    m = MagicMock()
    m.return_value.json.return_value = reviews
    m.return_value.raise_for_status.return_value = None
    return m


def _mock_post(decision: str = "approve") -> MagicMock:
    m = MagicMock()
    m.return_value.json.return_value = {"decision": decision, "status": f"{decision}d"}
    m.return_value.raise_for_status.return_value = None
    return m


# ---------------------------------------------------------------------------
# Happy path: empty queue
# ---------------------------------------------------------------------------

def test_review_no_pending():
    with patch("httpx.get", _mock_get([])):
        result = runner.invoke(app, ["review", "--url", "http://localhost:8000"])
    assert result.exit_code == 0
    assert "No pending reviews" in result.output


# ---------------------------------------------------------------------------
# --open flag
# ---------------------------------------------------------------------------

def test_review_open_browser():
    with patch("webbrowser.open") as mock_open:
        result = runner.invoke(app, ["review", "--url", "http://localhost:8000", "--open"])
    assert result.exit_code == 0
    mock_open.assert_called_once()
    assert "localhost:8000" in mock_open.call_args.args[0]
    assert "/reviews" in mock_open.call_args.args[0]


def test_review_open_does_not_call_api():
    with patch("webbrowser.open"), patch("httpx.get") as mock_get:
        runner.invoke(app, ["review", "--url", "http://localhost:8000", "--open"])
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_review_connect_error():
    import httpx
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = runner.invoke(app, ["review", "--url", "http://localhost:8000"])
    assert result.exit_code == 1
    assert "antcrew serve" in result.output or "connect" in result.output.lower()


def test_review_http_error():
    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    with patch("httpx.get", side_effect=httpx.HTTPStatusError(
        "Unauthorized", request=MagicMock(), response=mock_resp
    )):
        result = runner.invoke(app, ["review", "--url", "http://localhost:8000"])
    assert result.exit_code == 1
    assert "401" in result.output


# ---------------------------------------------------------------------------
# Single review: auto-selected
# ---------------------------------------------------------------------------

def test_review_single_approve():
    mock_get = _mock_get([_REVIEW])
    mock_post = _mock_post("approve")
    with patch("httpx.get", mock_get), patch("httpx.post", mock_post):
        result = runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="approve\nn\n",
        )
    assert result.exit_code == 0
    call = mock_post.call_args
    payload = call.kwargs.get("json", {})
    assert payload.get("decision") == "approve"


def test_review_single_reject():
    mock_get = _mock_get([_REVIEW])
    mock_post = _mock_post("reject")
    with patch("httpx.get", mock_get), patch("httpx.post", mock_post):
        result = runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="reject\nn\n",
        )
    assert result.exit_code == 0
    payload = mock_post.call_args.kwargs.get("json", {})
    assert payload.get("decision") == "reject"


def test_review_shows_artifact_excerpt():
    """Artifact excerpt is shown in the listing."""
    mock_get = _mock_get([_REVIEW])
    mock_post = _mock_post()
    with patch("httpx.get", mock_get), patch("httpx.post", mock_post):
        result = runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="approve\nn\n",
        )
    assert "Ticket 1" in result.output


# ---------------------------------------------------------------------------
# With feedback
# ---------------------------------------------------------------------------

def test_review_with_feedback():
    mock_get = _mock_get([_REVIEW])
    mock_post = _mock_post("reject")
    with patch("httpx.get", mock_get), patch("httpx.post", mock_post):
        result = runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="reject\ny\nNeeds more detail\n",
        )
    assert result.exit_code == 0
    payload = mock_post.call_args.kwargs.get("json", {})
    assert payload.get("decision") == "reject"
    assert payload.get("feedback") == "Needs more detail"


def test_review_no_feedback_when_skipped():
    mock_get = _mock_get([_REVIEW])
    mock_post = _mock_post("approve")
    with patch("httpx.get", mock_get), patch("httpx.post", mock_post):
        runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="approve\nn\n",
        )
    payload = mock_post.call_args.kwargs.get("json", {})
    assert "feedback" not in payload


# ---------------------------------------------------------------------------
# Multiple reviews
# ---------------------------------------------------------------------------

_REVIEW_2 = {**_REVIEW, "review_id": "rev-def-222", "agent_name": "Dev Agent"}


def test_review_multiple_select_second():
    mock_get = _mock_get([_REVIEW, _REVIEW_2])
    mock_post = _mock_post("approve")
    with patch("httpx.get", mock_get), patch("httpx.post", mock_post):
        result = runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="2\napprove\nn\n",
        )
    assert result.exit_code == 0
    # POST URL should contain the second review's ID
    post_url = mock_post.call_args.args[0] if mock_post.call_args.args else ""
    assert "rev-def-222" in post_url


def test_review_multiple_exit_on_zero():
    mock_get = _mock_get([_REVIEW, _REVIEW_2])
    mock_post = _mock_post()
    with patch("httpx.get", mock_get), patch("httpx.post", mock_post):
        result = runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="0\n",
        )
    assert result.exit_code == 0
    mock_post.assert_not_called()


def test_review_multiple_invalid_selection():
    mock_get = _mock_get([_REVIEW, _REVIEW_2])
    with patch("httpx.get", mock_get):
        result = runner.invoke(
            app, ["review", "--url", "http://localhost:8000"],
            input="99\n",
        )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# --run filter
# ---------------------------------------------------------------------------

def test_review_run_filter_passes_param():
    mock_get = _mock_get([])
    with patch("httpx.get", mock_get):
        runner.invoke(app, ["review", "--url", "http://localhost:8000", "--run", "run-xyz-111"])
    params = mock_get.call_args.kwargs.get("params", {})
    assert params.get("run_id") == "run-xyz-111"


def test_review_without_run_filter_no_run_id_param():
    mock_get = _mock_get([])
    with patch("httpx.get", mock_get):
        runner.invoke(app, ["review", "--url", "http://localhost:8000"])
    params = mock_get.call_args.kwargs.get("params", {})
    assert "run_id" not in params


# ---------------------------------------------------------------------------
# API key header
# ---------------------------------------------------------------------------

def test_review_api_key_header():
    mock_get = _mock_get([])
    with patch("httpx.get", mock_get):
        runner.invoke(app, ["review", "--url", "http://localhost:8000", "--api-key", "s3cr3t"])
    headers = mock_get.call_args.kwargs.get("headers", {})
    assert headers.get("X-Api-Key") == "s3cr3t"


def test_review_no_api_key_empty_headers():
    mock_get = _mock_get([])
    with patch("httpx.get", mock_get):
        runner.invoke(app, ["review", "--url", "http://localhost:8000"])
    headers = mock_get.call_args.kwargs.get("headers", {})
    assert "X-Api-Key" not in headers


# ---------------------------------------------------------------------------
# review command is registered
# ---------------------------------------------------------------------------

def test_review_command_registered():
    result = runner.invoke(app, ["review", "--help"])
    assert result.exit_code == 0
    assert "pending" in result.output.lower() or "review" in result.output.lower()
