"""MCPTool — wrap MCP (Model Context Protocol) tool servers as AntCrew BaseTool.

Usage::

    from antcrew.tools.mcp import MCPTool, MCPToolset

    # Wrap a single known tool from an MCP server
    search = MCPTool(
        server_url="http://localhost:8080",
        tool_name="web_search",
        description="Search the web via a local MCP server.",
    )

    # Auto-discover all tools from an MCP server
    tools = MCPToolset.from_server("http://localhost:8080")
    agent = ResearcherAgent(llm, tools=tools)

Install: pip install antcrew[mcp]
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

try:
    import httpx as _httpx
    _AVAILABLE = True
except ImportError:
    _httpx = None  # type: ignore[assignment]
    _AVAILABLE = False

from antcrew.core.tools import BaseTool, ToolResult

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class MCPTool(BaseTool):
    """Wraps a single MCP-server tool as an AntCrew BaseTool.

    The tool makes synchronous HTTP calls to the MCP server's
    ``POST /tools/call`` endpoint (MCP HTTP transport spec).

    Args:
        server_url:  Base URL of the MCP server (e.g. ``http://localhost:8080``).
        tool_name:   Name of the tool as advertised by the server.
        description: Human-readable description forwarded to the LLM schema.
        timeout:     HTTP timeout in seconds (default 30).
        api_key:     Optional Bearer token for authenticated MCP servers.
        namespace:   Optional namespace prefix for the tool name in LLM schemas,
                     e.g. ``"data"`` → tool name becomes ``"data/web_search"``.
    """

    def __init__(
        self,
        server_url: str,
        tool_name: str,
        description: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        api_key: Optional[str] = None,
        input_schema: Optional[dict] = None,
        namespace: str = "",
    ) -> None:
        if not _AVAILABLE:
            raise ImportError(
                "httpx is required for MCPTool.\n"
                "  pip install antcrew[mcp]\n"
                "  or: pip install httpx"
            )
        self._tool_name = tool_name          # canonical name sent to MCP server
        self.name = f"{namespace}/{tool_name}" if namespace else tool_name
        self.description = description
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key
        self._input_schema = input_schema or {}
        self.namespace = namespace

    def run(self, input: str) -> ToolResult:
        """Call the MCP tool. *input* may be plain text or a JSON dict string."""
        try:
            payload: Any = json.loads(input)
        except (json.JSONDecodeError, ValueError):
            payload = {"input": input}

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        request_body = {"name": self._tool_name, "arguments": payload}

        try:
            response = _httpx.post(
                f"{self._server_url}/tools/call",
                json=request_body,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            # MCP spec: {content: [{type: "text", text: "..."}], isError: bool}
            if data.get("isError"):
                error_text = _extract_text(data.get("content", []))
                return ToolResult("", error=error_text or "MCP tool returned an error")
            return ToolResult(_extract_text(data.get("content", [])))
        except Exception as exc:
            log.warning("mcp_tool_error tool=%s exc=%s", self._tool_name, exc)
            return ToolResult("", error=str(exc))

    def schema(self) -> str:
        """Return a compact JSON schema string for the ReAct tool-use loop."""
        return json.dumps({
            "name": self.name,
            "description": self.description,
            "input_schema": self._input_schema or {"type": "object", "properties": {"input": {"type": "string"}}},
        }, indent=2)


class MCPToolset(list):
    """A list of :class:`MCPTool` instances discovered from an MCP server.

    Usage::

        # All tools from a server
        tools = MCPToolset.from_server("http://localhost:8080")

        # With a namespace prefix so tools appear as "data/web_search" etc.
        tools = MCPToolset.from_server("http://localhost:8080", namespace="data")

        agent = ResearcherAgent(llm, tools=tools)
    """

    @classmethod
    def from_server(
        cls,
        server_url: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        api_key: Optional[str] = None,
        namespace: str = "",
    ) -> "MCPToolset":
        """Discover all tools from an MCP server's ``GET /tools/list`` endpoint.

        Args:
            server_url: Base URL of the MCP server.
            timeout:    HTTP timeout in seconds.
            api_key:    Optional Bearer token for authenticated servers.
            namespace:  Optional prefix applied to all discovered tool names,
                        e.g. ``"data"`` → ``"data/web_search"``.
        """
        if not _AVAILABLE:
            raise ImportError(
                "httpx is required for MCPToolset.\n"
                "  pip install antcrew[mcp]\n"
                "  or: pip install httpx"
            )
        server_url = server_url.rstrip("/")
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        response = _httpx.get(
            f"{server_url}/tools/list",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        tools_data = data.get("tools", data if isinstance(data, list) else [])
        instance = cls()
        instance._namespace = namespace
        for t in tools_data:
            instance.append(
                MCPTool(
                    server_url=server_url,
                    tool_name=t["name"],
                    description=t.get("description", t["name"]),
                    timeout=timeout,
                    api_key=api_key,
                    input_schema=t.get("inputSchema") or t.get("input_schema"),
                    namespace=namespace,
                )
            )
        return instance

    @property
    def namespace(self) -> str:
        """Namespace prefix applied to all tools in this toolset."""
        return getattr(self, "_namespace", "")


# ---------------------------------------------------------------------------

def _extract_text(content: list[dict]) -> str:
    """Extract text from MCP content blocks."""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


class MCPRegistry:
    """Named registry of :class:`MCPToolset` instances grouped by namespace.

    Useful when an agent needs tools from multiple MCP servers and you want to
    keep them organised by domain::

        registry = MCPRegistry()
        registry.register(MCPToolset.from_server("http://data-svc:8080",   namespace="data"))
        registry.register(MCPToolset.from_server("http://exec-svc:8081",   namespace="exec"))
        registry.register(MCPToolset.from_server("http://search-svc:8082", namespace="search"))

        agent = ResearcherAgent(llm, tools=registry.all_tools())

        # Or pass only the tools from one namespace:
        agent = AnalysisAgent(llm, tools=registry.ns("data"))
    """

    def __init__(self) -> None:
        self._toolsets: dict[str, MCPToolset] = {}

    def register(
        self,
        toolset: MCPToolset,
        namespace: str = "",
    ) -> "MCPRegistry":
        """Add a toolset to the registry under *namespace*.

        If *namespace* is empty, falls back to the toolset's own namespace.
        Returns ``self`` to allow chaining::

            registry = (
                MCPRegistry()
                .register(ts_data, "data")
                .register(ts_exec, "exec")
            )
        """
        key = namespace or toolset.namespace or ""
        self._toolsets[key] = toolset
        return self

    def ns(self, namespace: str) -> list[MCPTool]:
        """Return tools registered under *namespace* (empty list if not found)."""
        return list(self._toolsets.get(namespace, []))

    def namespaces(self) -> list[str]:
        """Return all registered namespace keys."""
        return list(self._toolsets.keys())

    def all_tools(self) -> list[MCPTool]:
        """Return every tool across all namespaces as a flat list."""
        result: list[MCPTool] = []
        for ts in self._toolsets.values():
            result.extend(ts)
        return result

    def __len__(self) -> int:
        return sum(len(ts) for ts in self._toolsets.values())

    def __repr__(self) -> str:
        parts = ", ".join(
            f"{k!r}({len(v)})" for k, v in self._toolsets.items()
        )
        return f"MCPRegistry({parts})"
