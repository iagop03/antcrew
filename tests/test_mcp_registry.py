"""Tests for MCPTool namespace, MCPToolset.namespace, and MCPRegistry."""
from __future__ import annotations

import json
import pytest

from antcrew.tools.mcp import MCPTool, MCPToolset, MCPRegistry


# ---------------------------------------------------------------------------
# Helpers — build MCPTool instances without an HTTP server
# ---------------------------------------------------------------------------

def _tool(name: str, description: str = "desc", namespace: str = "") -> MCPTool:
    return MCPTool(
        server_url="http://localhost:9999",
        tool_name=name,
        description=description,
        namespace=namespace,
    )


def _toolset(tools_data: list[dict], namespace: str = "") -> MCPToolset:
    ts = MCPToolset()
    ts._namespace = namespace
    for t in tools_data:
        ts.append(
            MCPTool(
                server_url="http://localhost:9999",
                tool_name=t["name"],
                description=t.get("description", t["name"]),
                namespace=namespace,
            )
        )
    return ts


# ---------------------------------------------------------------------------
# MCPTool — namespace
# ---------------------------------------------------------------------------

def test_mcp_tool_no_namespace_uses_plain_name():
    tool = _tool("web_search")
    assert tool.name == "web_search"
    assert tool._tool_name == "web_search"
    assert tool.namespace == ""


def test_mcp_tool_namespace_prefixes_name():
    tool = _tool("web_search", namespace="data")
    assert tool.name == "data/web_search"


def test_mcp_tool_namespace_preserves_tool_name_for_server():
    """MCP server still receives the original name, not the prefixed one."""
    tool = _tool("web_search", namespace="data")
    assert tool._tool_name == "web_search"


def test_mcp_tool_namespace_attribute():
    tool = _tool("web_search", namespace="exec")
    assert tool.namespace == "exec"


def test_mcp_tool_request_body_uses_original_name(monkeypatch):
    """HTTP request to MCP server must use _tool_name, not the namespaced name."""
    import antcrew.tools.mcp as _mcp_mod

    captured = {}

    def _fake_post(url, *, json, headers, timeout):
        captured["body"] = json

        class _R:
            def raise_for_status(self): pass
            def json(self): return {"isError": False, "content": [{"type": "text", "text": "ok"}]}
        return _R()

    monkeypatch.setattr(_mcp_mod._httpx, "post", _fake_post)

    tool = _tool("web_search", namespace="data")
    tool.run('{"query": "cats"}')

    assert captured["body"]["name"] == "web_search"       # original, not "data/web_search"
    assert captured["body"]["arguments"]["query"] == "cats"


def test_mcp_tool_schema_uses_namespaced_name():
    tool = _tool("web_search", namespace="data")
    schema = json.loads(tool.schema())
    assert schema["name"] == "data/web_search"


# ---------------------------------------------------------------------------
# MCPToolset — namespace property
# ---------------------------------------------------------------------------

def test_toolset_namespace_defaults_to_empty():
    ts = MCPToolset()
    assert ts.namespace == ""


def test_toolset_namespace_reflects_private_attr():
    ts = _toolset([{"name": "search"}], namespace="data")
    assert ts.namespace == "data"


def test_toolset_tools_inherit_namespace():
    ts = _toolset([{"name": "search"}, {"name": "fetch"}], namespace="data")
    assert all(t.namespace == "data" for t in ts)
    assert all(t.name.startswith("data/") for t in ts)


def test_toolset_is_a_list():
    ts = _toolset([{"name": "a"}, {"name": "b"}])
    assert len(ts) == 2
    assert isinstance(ts, list)


def test_toolset_from_server_applies_namespace(monkeypatch):
    """MCPToolset.from_server with namespace= prefixes all tool names."""
    import antcrew.tools.mcp as _mcp_mod

    def _fake_get(url, *, headers, timeout):
        class _R:
            def raise_for_status(self): pass
            def json(self):
                return {"tools": [
                    {"name": "web_search", "description": "Search"},
                    {"name": "fetch_url",  "description": "Fetch"},
                ]}
        return _R()

    monkeypatch.setattr(_mcp_mod._httpx, "get", _fake_get)

    ts = MCPToolset.from_server("http://localhost:9999", namespace="data")
    assert ts.namespace == "data"
    assert len(ts) == 2
    assert ts[0].name == "data/web_search"
    assert ts[1].name == "data/fetch_url"
    assert ts[0]._tool_name == "web_search"


# ---------------------------------------------------------------------------
# MCPRegistry
# ---------------------------------------------------------------------------

def test_registry_empty():
    registry = MCPRegistry()
    assert len(registry) == 0
    assert registry.namespaces() == []
    assert registry.all_tools() == []


def test_registry_register_by_namespace():
    registry = MCPRegistry()
    ts = _toolset([{"name": "search"}], namespace="data")
    registry.register(ts, "data")
    assert "data" in registry.namespaces()
    assert len(registry.ns("data")) == 1


def test_registry_register_falls_back_to_toolset_namespace():
    registry = MCPRegistry()
    ts = _toolset([{"name": "search"}], namespace="data")
    registry.register(ts)  # no explicit namespace — uses ts.namespace
    assert "data" in registry.namespaces()


def test_registry_ns_returns_empty_list_for_unknown():
    registry = MCPRegistry()
    assert registry.ns("nonexistent") == []


def test_registry_ns_returns_correct_tools():
    registry = MCPRegistry()
    ts_data = _toolset([{"name": "search"}, {"name": "fetch"}], namespace="data")
    ts_exec = _toolset([{"name": "run_code"}], namespace="exec")
    registry.register(ts_data, "data")
    registry.register(ts_exec, "exec")

    data_tools = registry.ns("data")
    assert len(data_tools) == 2
    assert all(t.namespace == "data" for t in data_tools)

    exec_tools = registry.ns("exec")
    assert len(exec_tools) == 1
    assert exec_tools[0]._tool_name == "run_code"


def test_registry_all_tools_combines_namespaces():
    registry = MCPRegistry()
    ts_a = _toolset([{"name": "a1"}, {"name": "a2"}], namespace="ns_a")
    ts_b = _toolset([{"name": "b1"}], namespace="ns_b")
    registry.register(ts_a, "ns_a")
    registry.register(ts_b, "ns_b")
    all_tools = registry.all_tools()
    assert len(all_tools) == 3


def test_registry_len():
    registry = MCPRegistry()
    ts_a = _toolset([{"name": "a"}, {"name": "b"}], namespace="ns_a")
    ts_b = _toolset([{"name": "c"}], namespace="ns_b")
    registry.register(ts_a)
    registry.register(ts_b)
    assert len(registry) == 3


def test_registry_is_chainable():
    ts_a = _toolset([{"name": "a"}], namespace="ns_a")
    ts_b = _toolset([{"name": "b"}], namespace="ns_b")
    registry = MCPRegistry().register(ts_a).register(ts_b)
    assert len(registry) == 2


def test_registry_namespaces_lists_keys():
    registry = MCPRegistry()
    registry.register(_toolset([{"name": "x"}], namespace="alpha"), "alpha")
    registry.register(_toolset([{"name": "y"}], namespace="beta"),  "beta")
    ns = registry.namespaces()
    assert set(ns) == {"alpha", "beta"}


def test_registry_overwrite_namespace():
    """Registering a second toolset under the same namespace replaces the first."""
    registry = MCPRegistry()
    ts1 = _toolset([{"name": "old"}], namespace="data")
    ts2 = _toolset([{"name": "new1"}, {"name": "new2"}], namespace="data")
    registry.register(ts1, "data")
    registry.register(ts2, "data")
    assert len(registry.ns("data")) == 2
    assert len(registry) == 2


def test_registry_repr_is_readable():
    registry = MCPRegistry()
    registry.register(_toolset([{"name": "a"}, {"name": "b"}], namespace="data"), "data")
    r = repr(registry)
    assert "MCPRegistry" in r
    assert "data" in r
    assert "2" in r
