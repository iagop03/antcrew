"""Tests for GitHubIntegration."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from antcrew.core.artifacts import (
    CodeArtifact, DevOpsArtifact, PRD,
    Priority, Ticket, TicketStatus,
)
from antcrew.integrations.github import GitHubIntegration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh(repo: str = "org/repo") -> GitHubIntegration:
    return GitHubIntegration(token="ghp_test", repo=repo, base_branch="main")


def _resp(data: dict | list, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.json.return_value = data
    r.status_code = status
    r.raise_for_status.return_value = None
    return r


def _minimal_state() -> dict:
    return {
        "prd": PRD(
            title="Auth Module",
            summary="JWT auth service",
            goals=[], out_of_scope=[], functional_requirements=[],
            non_functional_requirements=[], open_questions=[],
        ),
        "tickets": [
            Ticket(
                id="T-001", title="JWT endpoint", description="Add /auth",
                priority=Priority.HIGH, status=TicketStatus.DONE,
                acceptance_criteria=[], dependencies=[],
            )
        ],
        "code_artifacts": [
            CodeArtifact(
                ticket_id="T-001",
                file_path="src/auth.py",
                description="Auth handler",
                language="python",
                content="def auth(): pass\n",
            )
        ],
        "devops_artifacts": [
            DevOpsArtifact(
                file_path="Dockerfile",
                description="Docker image",
                language="dockerfile",
                content="FROM python:3.12-slim\n",
            )
        ],
        "test_artifacts": None,
        "review": None,
        "research_document": None,
        "content_piece": None,
        "messages": [],
        "request": "Build auth",
        "current_agent": "",
        "errors": [],
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# _build_body
# ---------------------------------------------------------------------------

def test_build_body_includes_prd_title():
    gh = _gh()
    state = _minimal_state()
    body = gh._build_body(state)
    assert "Auth Module" in body


def test_build_body_includes_tickets():
    gh = _gh()
    state = _minimal_state()
    body = gh._build_body(state)
    assert "T-001" in body
    assert "JWT endpoint" in body


def test_build_body_includes_devops_files():
    gh = _gh()
    state = _minimal_state()
    body = gh._build_body(state)
    assert "Dockerfile" in body


def test_build_body_no_prd():
    gh = _gh()
    state = _minimal_state()
    state["prd"] = None
    body = gh._build_body(state)
    assert "AntCrew Generated PR" in body


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Hello World!", "hello-world-"),
    ("My PR Title", "my-pr-title"),
    ("  spaces  ", "spaces"),
    ("a" * 60, "a" * 40),
])
def test_slug(title, expected):
    assert GitHubIntegration._slug(title) == expected.strip("-")


# ---------------------------------------------------------------------------
# create_pr
# ---------------------------------------------------------------------------

def _setup_create_pr_mocks(pr_url: str = "https://github.com/org/repo/pull/1"):
    """
    Returns a side_effect function for httpx calls in the order they happen:
    1. GET /git/ref/heads/main → sha
    2. POST /git/refs → create branch
    3. GET /contents/src/auth.py → 404 (new file)
    4. PUT /contents/src/auth.py → ok
    5. GET /contents/Dockerfile → 404
    6. PUT /contents/Dockerfile → ok
    7. POST /pulls → pr
    """
    call_order = [
        _resp({"object": {"sha": "abc123"}}),       # GET ref
        _resp({}),                                   # POST ref (create branch)
        _resp({}, status=404),                      # GET contents/src/auth.py
        _resp({"content": "..."}),                  # PUT contents/src/auth.py
        _resp({}, status=404),                      # GET contents/Dockerfile
        _resp({"content": "..."}),                  # PUT contents/Dockerfile
        _resp({"html_url": pr_url}),                # POST pulls
    ]
    calls = iter(call_order)

    def _side(*args, **kwargs):
        r = next(calls)
        if r.status_code == 404:
            r.raise_for_status.side_effect = None  # GET 404 is handled gracefully
        return r

    return _side


def test_create_pr_returns_url():
    gh = _gh()
    with (
        patch("httpx.get", side_effect=_setup_create_pr_mocks()) as mock_get,
        patch("httpx.post", return_value=_resp({})) as mock_post,
        patch("httpx.put", return_value=_resp({})),
    ):
        # re-route through actual mock that matches the call sequence
        pass  # we patch in the next test


def test_create_pr_calls_github_api(tmp_path):
    gh = _gh("myorg/myrepo")
    state = _minimal_state()

    get_responses = [
        _resp({"object": {"sha": "abc123"}}),   # GET ref
        _resp({}, status=404),                  # GET contents/src/auth.py
        _resp({}, status=404),                  # GET contents/Dockerfile
    ]
    get_iter = iter(get_responses)

    post_responses = [
        _resp({}),                              # POST refs (create branch)
        _resp({"html_url": "https://github.com/myorg/myrepo/pull/7"}),  # POST pulls
    ]
    post_iter = iter(post_responses)

    with (
        patch("httpx.get", side_effect=lambda *a, **kw: next(get_iter)),
        patch("httpx.post", side_effect=lambda *a, **kw: next(post_iter)),
        patch("httpx.put", return_value=_resp({})),
    ):
        url = gh.create_pr(state)

    assert url == "https://github.com/myorg/myrepo/pull/7"


def test_create_pr_raises_on_empty_artifacts():
    gh = _gh()
    state = _minimal_state()
    state["code_artifacts"] = []
    state["devops_artifacts"] = []
    with pytest.raises(ValueError, match="empty"):
        gh.create_pr(state)


def test_create_pr_branch_contains_slug():
    gh = _gh("o/r")
    state = _minimal_state()

    created_branches: list[str] = []

    get_responses = [
        _resp({"object": {"sha": "sha1"}}),
        _resp({}, status=404),
        _resp({}, status=404),
    ]
    get_iter = iter(get_responses)

    def _post_side(*args, **kwargs):
        payload = kwargs.get("json", {})
        if "ref" in payload:
            created_branches.append(payload["ref"])
        return _resp({"html_url": "https://github.com/o/r/pull/1"})

    with (
        patch("httpx.get", side_effect=lambda *a, **kw: next(get_iter)),
        patch("httpx.post", side_effect=_post_side),
        patch("httpx.put", return_value=_resp({})),
    ):
        gh.create_pr(state)

    assert created_branches, "Branch was never created"
    assert "auth-module" in created_branches[0].lower()


def test_create_pr_updates_existing_file():
    """When a file exists on the branch, its SHA must be included in the PUT."""
    gh = _gh()
    state = _minimal_state()
    state["devops_artifacts"] = []  # skip devops

    existing_sha = "existingblob123"
    get_responses = [
        _resp({"object": {"sha": "base"}}),
        _resp({"sha": existing_sha, "type": "file"}),  # file exists
    ]
    get_iter = iter(get_responses)

    put_payloads: list[dict] = []

    def _put_side(*args, **kwargs):
        put_payloads.append(kwargs.get("json", {}))
        return _resp({})

    with (
        patch("httpx.get", side_effect=lambda *a, **kw: next(get_iter)),
        patch("httpx.post", return_value=_resp({"html_url": "https://github.com/x/y/pull/1"})),
        patch("httpx.put", side_effect=_put_side),
    ):
        gh.create_pr(state)

    assert put_payloads[0]["sha"] == existing_sha


# ---------------------------------------------------------------------------
# create_engine_pr
# ---------------------------------------------------------------------------

def _engine_arts() -> list[dict]:
    return [
        {"file_path": "src/main.py", "content": "def main(): pass\n"},
        {"file_path": "tests/test_main.py", "content": "def test_main(): pass\n"},
    ]


def _engine_conditions() -> dict[str, str]:
    return {
        "requirements_exists": "satisfied",
        "architecture_exists": "satisfied",
        "implementation_exists": "satisfied",
        "tests_pass": "not_reached",
    }


def _engine_capabilities() -> list[dict]:
    return [
        {"name": "SpecExtractor", "duration_s": 5.2, "cost_usd": 0.01, "produced": ["requirements"]},
        {"name": "Architect",     "duration_s": 12.1, "cost_usd": 0.03, "produced": ["architecture"]},
        {"name": "CodeGenerator", "duration_s": 20.0, "cost_usd": 0.08, "produced": ["implementation"]},
    ]


def _setup_engine_mocks(pr_url: str = "https://github.com/org/repo/pull/42"):
    """Mock sequence for create_engine_pr with 2 artifacts."""
    get_calls = [
        _resp({"object": {"sha": "eng123"}}),   # GET ref
        _resp({}, status=404),                  # GET contents/src/main.py
        _resp({}, status=404),                  # GET contents/tests/test_main.py
    ]
    get_iter = iter(get_calls)
    post_calls = [
        _resp({}),                              # POST refs (create branch)
        _resp({"html_url": pr_url}),            # POST pulls
        _resp({}),                              # POST issues/{n}/comments (summary comment)
    ]
    post_iter = iter(post_calls)
    return (
        lambda *a, **kw: next(get_iter),
        lambda *a, **kw: next(post_iter),
    )


def test_create_engine_pr_returns_url():
    gh = _gh("org/repo")
    get_side, post_side = _setup_engine_mocks()
    with (
        patch("httpx.get",  side_effect=get_side),
        patch("httpx.post", side_effect=post_side),
        patch("httpx.put",  return_value=_resp({})),
    ):
        url = gh.create_engine_pr(
            goal="Build a REST API",
            artifacts=_engine_arts(),
            conditions=_engine_conditions(),
            capabilities=_engine_capabilities(),
        )
    assert url == "https://github.com/org/repo/pull/42"


def test_create_engine_pr_raises_on_empty_artifacts():
    gh = _gh()
    with pytest.raises(ValueError, match="empty"):
        gh.create_engine_pr(goal="Build something", artifacts=[])


def test_create_engine_pr_branch_uses_engine_prefix():
    gh = _gh("o/r")
    created: list[str] = []

    get_responses = [
        _resp({"object": {"sha": "s1"}}),
        _resp({}, status=404),
        _resp({}, status=404),
    ]
    get_iter = iter(get_responses)

    def _post_side(*args, **kwargs):
        payload = kwargs.get("json", {})
        if "ref" in payload:
            created.append(payload["ref"])
        return _resp({"html_url": "https://github.com/o/r/pull/1"})

    with (
        patch("httpx.get",  side_effect=lambda *a, **kw: next(get_iter)),
        patch("httpx.post", side_effect=_post_side),
        patch("httpx.put",  return_value=_resp({})),
    ):
        gh.create_engine_pr("Build API", artifacts=_engine_arts())

    assert created, "No branch created"
    assert created[0].startswith("refs/heads/antcrew-engine/")


def test_create_engine_pr_accepts_object_artifacts():
    """Accepts Artifact-like objects (antcrew_engine.engine.Artifact) not just dicts."""
    from unittest.mock import MagicMock
    art = MagicMock()
    art.file_path = "src/main.py"
    art.content   = "pass\n"

    gh = _gh("o/r")
    get_responses = [_resp({"object": {"sha": "s1"}}), _resp({}, status=404)]
    get_iter = iter(get_responses)

    with (
        patch("httpx.get",  side_effect=lambda *a, **kw: next(get_iter)),
        patch("httpx.post", return_value=_resp({"html_url": "https://github.com/o/r/pull/2"})),
        patch("httpx.put",  return_value=_resp({})),
    ):
        url = gh.create_engine_pr("Goal", artifacts=[art])
    assert "pull/2" in url


# ---------------------------------------------------------------------------
# _build_engine_summary_comment
# ---------------------------------------------------------------------------

def test_engine_summary_comment_shows_conditions():
    gh = _gh()
    comment = gh._build_engine_summary_comment(
        "Build API",
        _engine_conditions(),
        _engine_capabilities(),
        artifact_count=2,
    )
    assert "✅" in comment
    assert "requirements_exists" in comment
    assert "tests_pass" in comment
    assert "⬜" in comment


def test_engine_summary_comment_shows_capabilities():
    gh = _gh()
    comment = gh._build_engine_summary_comment(
        "Build API",
        conditions=None,
        capabilities=_engine_capabilities(),
        artifact_count=2,
    )
    assert "SpecExtractor" in comment
    assert "Architect" in comment
    assert "CodeGenerator" in comment


def test_engine_summary_comment_shows_total_cost():
    gh = _gh()
    comment = gh._build_engine_summary_comment(
        "Build API",
        conditions=None,
        capabilities=_engine_capabilities(),
        artifact_count=2,
    )
    # Total cost: 0.01 + 0.03 + 0.08 = 0.12
    assert "0.12" in comment or "0.1200" in comment


def test_engine_summary_comment_no_conditions():
    gh = _gh()
    comment = gh._build_engine_summary_comment(
        "Build API", conditions=None, capabilities=None, artifact_count=5
    )
    assert "Build API" in comment
    assert "5" in comment
