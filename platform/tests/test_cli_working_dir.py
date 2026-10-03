"""CLI working directory — workspace integration tests.

CWD01  GET /workspaces/{id}/cli-working-dir → 200 null when unset
CWD02  PATCH /workspaces/{id}/cli-working-dir sets a path → reflected in GET
CWD03  PATCH with null clears the field
CWD04  PATCH on non-existent workspace → 404
CWD05  _make_team passes extra_body={"working_directory": ...} to build_llm when cli_working_dir is set
CWD06  _make_team with no cli_working_dir does not inject extra_body
CWD07  _run_sync forwards cli_working_dir to _make_team
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.core.auth import WorkspaceContext, get_workspace_context
from app.main import app
from app.models.workspace import Workspace


def _ws_ctx(workspace_id: int = 1) -> WorkspaceContext:
    return WorkspaceContext(workspace_id=workspace_id, created_by="test", role="admin")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_workspace_context] = lambda: _ws_ctx(1)
    yield
    app.dependency_overrides.pop(get_workspace_context, None)


async def _make_ws(session, *, cli_working_dir=None) -> Workspace:
    ws = Workspace(
        name="CLI WS",
        slug=f"cli-ws-{uuid.uuid4().hex[:6]}",
        cli_working_dir=cli_working_dir,
    )
    session.add(ws)
    await session.flush()
    return ws


# ---------------------------------------------------------------------------
# CWD01 — GET returns null when unset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cwd01_get_unset(client, session):
    ws = await _make_ws(session)
    r = await client.get(f"/workspaces/{ws.id}/cli-working-dir")
    assert r.status_code == 200
    assert r.json()["cli_working_dir"] is None
    assert r.json()["workspace_id"] == ws.id


# ---------------------------------------------------------------------------
# CWD02 — PATCH sets a path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cwd02_patch_sets_path(client, session):
    ws = await _make_ws(session)
    r = await client.patch(
        f"/workspaces/{ws.id}/cli-working-dir",
        json={"cli_working_dir": "/home/dev/projects/acme"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["cli_working_dir"] == "/home/dev/projects/acme"

    # GET reflects the change
    r2 = await client.get(f"/workspaces/{ws.id}/cli-working-dir")
    assert r2.json()["cli_working_dir"] == "/home/dev/projects/acme"


# ---------------------------------------------------------------------------
# CWD03 — PATCH with null clears the field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cwd03_patch_clears(client, session):
    ws = await _make_ws(session, cli_working_dir="/some/path")
    r = await client.patch(
        f"/workspaces/{ws.id}/cli-working-dir",
        json={"cli_working_dir": None},
    )
    assert r.status_code == 200
    assert r.json()["cli_working_dir"] is None


# ---------------------------------------------------------------------------
# CWD04 — PATCH on missing workspace → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cwd04_patch_missing_workspace(client):
    r = await client.patch(
        "/workspaces/999999/cli-working-dir",
        json={"cli_working_dir": "/some/path"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# CWD05 — _make_team passes extra_body when cli_working_dir is set
# ---------------------------------------------------------------------------

def test_cwd05_make_team_extra_body():
    captured = {}

    def _fake_build_llm(model, **kw):
        captured.update(kw)
        llm = MagicMock()
        llm.max_cost_usd = None
        return llm

    def _fake_team(**kw):
        t = MagicMock()
        t._agents = {}
        t.trace_log = None
        return t

    from app.services.runner import _TEAM_REGISTRY
    first_key = next(iter(_TEAM_REGISTRY))

    with patch("app.services.runner._TEAM_REGISTRY", {first_key: _TEAM_REGISTRY[first_key]}), \
         patch("antcrew.build_llm", _fake_build_llm):
        import importlib
        import antcrew.config as _cfg
        orig_mod, orig_cls = _TEAM_REGISTRY[first_key]
        mod = importlib.import_module(orig_mod)
        cls = getattr(mod, orig_cls)

        with patch.object(cls, "__init__", lambda self, **kw: None):
            from app.services.runner import _make_team
            _make_team(first_key, cli_working_dir="/workspace/acme")

    assert captured.get("extra_body") == {"working_directory": "/workspace/acme"}


# ---------------------------------------------------------------------------
# CWD06 — _make_team with no cli_working_dir does not inject extra_body
# ---------------------------------------------------------------------------

def test_cwd06_make_team_no_extra_body():
    captured = {}

    def _fake_build_llm(model, **kw):
        captured.update(kw)
        llm = MagicMock()
        llm.max_cost_usd = None
        return llm

    from app.services.runner import _TEAM_REGISTRY
    first_key = next(iter(_TEAM_REGISTRY))
    import importlib
    orig_mod, orig_cls = _TEAM_REGISTRY[first_key]
    mod = importlib.import_module(orig_mod)
    cls = getattr(mod, orig_cls)

    with patch("antcrew.build_llm", _fake_build_llm), \
         patch.object(cls, "__init__", lambda self, **kw: None):
        from app.services.runner import _make_team
        _make_team(first_key, byok_api_key="sk-test")

    assert "extra_body" not in captured


# ---------------------------------------------------------------------------
# CWD07 — _run_sync forwards cli_working_dir to _make_team
# ---------------------------------------------------------------------------

def test_cwd07_run_sync_forwards_cli_working_dir():
    call_kwargs = {}

    def _fake_make_team(team_name, **kw):
        call_kwargs.update(kw)
        t = MagicMock()
        t._agents = {}
        result = MagicMock()
        result.state = {"_run_id": "test-run"}
        result.cost_usd = 0.0
        result.usage = {}
        t.run.return_value = result
        return t

    import app.services.runner_core as runner_core_mod
    with patch.object(runner_core_mod, "_make_team", _fake_make_team):
        from app.services.runner import _run_sync
        from app.services.runner import PlatformChannel
        ch = PlatformChannel()
        _run_sync(
            "DevTeam", "build auth", "t1",
            None, ch, False,
            cli_working_dir="/home/dev/proj",
        )

    assert call_kwargs.get("cli_working_dir") == "/home/dev/proj"
