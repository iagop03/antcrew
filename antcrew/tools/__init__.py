"""antcrew.tools — extended tool integrations.

Built-in tools are in antcrew.core.tools (BaseTool, WebSearchTool, etc.).
This package adds protocol-level integrations:

    antcrew.tools.mcp  — wrap MCP (Model Context Protocol) tool servers as BaseTool.
"""
from antcrew.tools.mcp import MCPTool, MCPToolset

__all__ = ["MCPTool", "MCPToolset"]
