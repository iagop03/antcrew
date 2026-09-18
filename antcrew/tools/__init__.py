"""antcrew.tools — extended tool integrations.

Built-in tools are in antcrew.core.tools (BaseTool, WebSearchTool, etc.).
This package adds protocol-level integrations:

    antcrew.tools.mcp  — wrap MCP (Model Context Protocol) tool servers as BaseTool.
"""
from antcrew.tools.java_to_cobol_tool import JavaToCOBOLTool
from antcrew.tools.mcp import MCPRegistry, MCPTool, MCPToolset

__all__ = ["MCPRegistry", "MCPTool", "MCPToolset", "JavaToCOBOLTool"]
