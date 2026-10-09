"""Unit tests for workspace access control helpers.

Tests ws_accessible() and _assert_run_access() in isolation — no HTTP client,
no database. These complement the integration tests in test_cross_tenant.py.
"""
from __future__ import annotations

import pytest

from app.core.auth import WorkspaceContext, ws_accessible
from app.core.exceptions import RunNotAccessibleError


class _FakeRun:
    """Minimal run stub for _assert_run_access."""
    def __init__(self, workspace_id):
        self.workspace_id = workspace_id


def _scoped(workspace_id, *, extra_ids=None):
    return WorkspaceContext(
        workspace_id=workspace_id,
        created_by="k",
        membership_ids=extra_ids or [],
    )


def _global():
    return WorkspaceContext(workspace_id=None, created_by="env_key", role="admin")


# ---------------------------------------------------------------------------
# ws_accessible
# ---------------------------------------------------------------------------

def test_ws_accessible_own_workspace():
    assert ws_accessible(1, _scoped(1)) is True


def test_ws_accessible_other_workspace_denied():
    assert ws_accessible(2, _scoped(1)) is False


def test_ws_accessible_global_allows_any():
    assert ws_accessible(99, _global()) is True
    assert ws_accessible(None, _global()) is True


def test_ws_accessible_membership_grants_access():
    assert ws_accessible(2, _scoped(1, extra_ids=[2, 3])) is True
    assert ws_accessible(3, _scoped(1, extra_ids=[2, 3])) is True


def test_ws_accessible_membership_does_not_include_non_member():
    assert ws_accessible(4, _scoped(1, extra_ids=[2, 3])) is False


# ---------------------------------------------------------------------------
# _assert_run_access
# ---------------------------------------------------------------------------

def _assert(run_ws, ctx):
    from app.api.runs import _assert_run_access
    _assert_run_access(_FakeRun(run_ws), ctx)


def test_assert_run_access_own_workspace_passes():
    _assert(1, _scoped(1))


def test_assert_run_access_cross_workspace_raises_403():
    with pytest.raises(RunNotAccessibleError) as exc_info:
        _assert(2, _scoped(1))
    assert exc_info.value.status_code == 403


def test_assert_run_access_global_ctx_passes_any():
    _assert(42, _global())
    _assert(None, _global())


def test_assert_run_access_membership_passes():
    _assert(2, _scoped(1, extra_ids=[2]))


def test_assert_run_access_non_member_raises():
    with pytest.raises(RunNotAccessibleError):
        _assert(3, _scoped(1, extra_ids=[2]))
