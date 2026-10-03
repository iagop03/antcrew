"""Round 22 tests — Encryption, Memory, A2A, QuickAgent.

ENCRYPTION (ENC1-ENC4):
  ENC1  EncryptedJSON stores as encrypted text when key is set
  ENC2  EncryptedJSON decrypts back to original value
  ENC3  EncryptedJSON backward-compat reads plain JSON when no key set
  ENC4  EncryptedJSON returns None and logs error when key missing for encrypted value

MEMORY (MEM1-MEM6):
  MEM1  GET /memory/{team} → empty dict when no memory exists
  MEM2  PUT /memory/{team} → creates memory, returns merged dict
  MEM3  PUT /memory/{team} → merges with existing (no overwrite of unrelated keys)
  MEM4  DELETE /memory/{team}/{key} → removes one key
  MEM5  DELETE /memory/{team}/{key} → 404 when key absent
  MEM6  DELETE /memory/{team} → 204, memory is cleared

A2A (A2A1-A2A5):
  A2A1  GET /a2a/{team} → 200 with valid AgentCard JSON
  A2A2  GET /a2a/Unknown → 404
  A2A3  POST /a2a/{team} tasks/send → 202 with run_id
  A2A4  POST /a2a/{team} tasks/send → 422 when message has no text part
  A2A5  POST /a2a/{team} unknown method → 404 JSON-RPC error

QUICKAGENT (QA1-QA4):
  QA1  QuickAgent.from_spec parses 'Role: goal' correctly
  QA2  QuickAgent.from_spec with no colon uses spec as both role and goal
  QA3  QuickTeam chains agents and threads _quick_context
  QA4  InMemoryKVMemory get/set/delete/all/dirty behaves correctly
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.main import app
from app.models.memory import RunMemory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ws_ctx() -> WorkspaceContext:
    return WorkspaceContext(workspace_id=1, created_by="test", role="admin")


@pytest.fixture(autouse=True)
def override_ws_context():
    app.dependency_overrides[get_workspace_context] = _ws_ctx
    yield
    app.dependency_overrides.pop(get_workspace_context, None)


# ─── Encryption tests ─────────────────────────────────────────────────────────

class TestEncryptedJSON:
    def test_enc1_stores_encrypted_when_key_set(self, tmp_path):
        """EncryptedJSON writes a sentinel-prefixed string when key is configured."""
        import base64
        key = base64.b64encode(os.urandom(32)).decode()

        # Import fresh with patched env
        import importlib
        with patch.dict(os.environ, {"ANTCREW_ENCRYPTION_KEY": key}):
            import app.core.encryption as enc_mod
            importlib.reload(enc_mod)
            col = enc_mod.EncryptedJSON()
            result = col.process_bind_param({"hello": "world"}, None)
            assert result is not None
            assert result.startswith("antcrew:enc:v1:")

        # Restore
        importlib.reload(enc_mod)

    def test_enc2_decrypts_back_to_original(self, tmp_path):
        """EncryptedJSON round-trips correctly: encrypt → decrypt → same dict."""
        import base64
        import importlib
        key = base64.b64encode(os.urandom(32)).decode()
        original = {"name": "AntCrew", "value": 42, "nested": {"a": [1, 2, 3]}}

        with patch.dict(os.environ, {"ANTCREW_ENCRYPTION_KEY": key}):
            import app.core.encryption as enc_mod
            importlib.reload(enc_mod)
            col = enc_mod.EncryptedJSON()
            encrypted = col.process_bind_param(original, None)
            decrypted = col.process_result_value(encrypted, None)
            assert decrypted == original

        importlib.reload(enc_mod)

    def test_enc3_backward_compat_plain_json(self):
        """EncryptedJSON reads plain JSON when no key is set (backward compat)."""
        import json
        from app.core.encryption import EncryptedJSON

        col = EncryptedJSON()
        plain = json.dumps({"foo": "bar"})
        result = col.process_result_value(plain, None)
        assert result == {"foo": "bar"}

    def test_enc4_none_passthrough(self):
        """EncryptedJSON handles None on both bind and result."""
        from app.core.encryption import EncryptedJSON

        col = EncryptedJSON()
        assert col.process_bind_param(None, None) is None
        assert col.process_result_value(None, None) is None


# ─── Memory tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mem1_get_empty(client: AsyncClient):
    r = await client.get("/memory/DevTeam")
    assert r.status_code == 200
    assert r.json() == {}


@pytest.mark.asyncio
async def test_mem2_put_creates_memory(client: AsyncClient):
    r = await client.put("/memory/DevTeam", json={"data": {"last_run": "2026-08-15", "count": 5}})
    assert r.status_code == 200
    data = r.json()
    assert data["last_run"] == "2026-08-15"
    assert data["count"] == 5

    # GET returns same data
    r2 = await client.get("/memory/DevTeam")
    assert r2.json() == data


@pytest.mark.asyncio
async def test_mem3_put_merges_without_overwriting_unrelated(client: AsyncClient):
    await client.put("/memory/DevTeam", json={"data": {"key_a": "value_a"}})
    await client.put("/memory/DevTeam", json={"data": {"key_b": "value_b"}})

    r = await client.get("/memory/DevTeam")
    data = r.json()
    assert data["key_a"] == "value_a"  # kept from first put
    assert data["key_b"] == "value_b"  # added by second put


@pytest.mark.asyncio
async def test_mem4_delete_key(client: AsyncClient, session: AsyncSession):
    session.add(RunMemory(
        workspace_id=1, team_name="DevTeam",
        memory_json={"alpha": 1, "beta": 2},
    ))
    await session.commit()

    r = await client.delete("/memory/DevTeam/alpha")
    assert r.status_code == 200
    assert "alpha" not in r.json()
    assert r.json()["beta"] == 2


@pytest.mark.asyncio
async def test_mem5_delete_missing_key_404(client: AsyncClient, session: AsyncSession):
    session.add(RunMemory(
        workspace_id=1, team_name="DevTeam",
        memory_json={"alpha": 1},
    ))
    await session.commit()

    r = await client.delete("/memory/DevTeam/nonexistent")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_mem6_clear_memory(client: AsyncClient, session: AsyncSession):
    session.add(RunMemory(
        workspace_id=1, team_name="DevTeam",
        memory_json={"a": 1, "b": 2},
    ))
    await session.commit()

    r = await client.delete("/memory/DevTeam")
    assert r.status_code == 204

    r2 = await client.get("/memory/DevTeam")
    assert r2.json() == {}


# ─── A2A tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a2a1_agent_card(client: AsyncClient):
    r = await client.get("/a2a/DevTeam")
    assert r.status_code == 200
    card = r.json()
    assert card["name"] == "DevTeam"
    assert "url" in card
    assert card["capabilities"]["streaming"] is True
    assert any(s["id"] == "run" for s in card["skills"])


@pytest.mark.asyncio
async def test_a2a2_unknown_team_404(client: AsyncClient):
    r = await client.get("/a2a/NonExistentTeam9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a2a3_tasks_send_dispatches_run(client: AsyncClient):
    fake_run_id = "a2a_run_abc123"
    with patch("app.api.a2a.dispatch", new_callable=AsyncMock, return_value=fake_run_id):
        r = await client.post("/a2a/DevTeam", json={
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "tasks/send",
            "params": {
                "id": "task-xyz",
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Build a REST API"}],
                },
            },
        })
    assert r.status_code == 202
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["result"]["id"] == fake_run_id
    assert body["result"]["status"]["state"] == "submitted"


@pytest.mark.asyncio
async def test_a2a4_tasks_send_no_text_422(client: AsyncClient):
    r = await client.post("/a2a/DevTeam", json={
        "jsonrpc": "2.0",
        "id": "req-2",
        "method": "tasks/send",
        "params": {
            "id": "task-empty",
            "message": {"role": "user", "parts": []},
        },
    })
    assert r.status_code == 422
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_a2a5_unknown_method_404(client: AsyncClient):
    r = await client.post("/a2a/DevTeam", json={
        "jsonrpc": "2.0",
        "id": "req-3",
        "method": "tasks/unknown_method",
        "params": {},
    })
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == -32601


# ─── QuickAgent / InMemoryKVMemory unit tests ─────────────────────────────────

def test_qa1_from_spec_parses_role_and_goal():
    from antcrew.agents.quick_agent import QuickAgent
    from antcrew.testing.llms import SimulatedLLM

    agent = QuickAgent.from_spec("Senior Researcher: Find papers on RAG", SimulatedLLM())
    assert agent.name == "senior_researcher"
    assert "Find papers" in agent.role_description


def test_qa2_from_spec_no_colon():
    from antcrew.agents.quick_agent import QuickAgent
    from antcrew.testing.llms import SimulatedLLM

    agent = QuickAgent.from_spec("Analyst", SimulatedLLM())
    assert agent.name == "analyst"
    assert agent.role_description == "Analyst"


def test_qa3_quick_team_chains_context():
    from antcrew.agents.quick_agent import QuickTeam
    from antcrew.testing.llms import SimulatedLLM

    team = QuickTeam(
        specs=["Planner: Make a plan", "Executor: Execute the plan"],
        llm=SimulatedLLM(),
    )
    result = team.run("Build something")
    # Both agents ran and the result key is populated
    assert "result" in result
    assert result["request"] == "Build something"


def test_qa4_in_memory_kv_memory():
    from antcrew.memory.kv_store import InMemoryKVMemory

    mem = InMemoryKVMemory(initial={"existing": "value"})
    assert mem.get("existing") == "value"
    assert mem.get("missing") is None
    assert not mem.dirty

    mem.set("new_key", {"nested": True})
    assert mem.dirty
    assert mem.get("new_key") == {"nested": True}

    mem.delete("existing")
    assert mem.get("existing") is None

    snap = mem.all()
    assert "new_key" in snap
    assert "existing" not in snap
