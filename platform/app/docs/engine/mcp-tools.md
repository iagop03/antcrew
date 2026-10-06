# MCP tools — wrap any MCP server as a BaseTool

`MCPTool` and `MCPToolset` let AntCrew agents call tools exposed by any server that speaks the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) over HTTP.

`httpx` is already a core AntCrew dependency — no extra install needed.

---

## What is MCP?

MCP is an open protocol for exposing tools, resources, and prompts to AI agents. A single MCP server can wrap a database, a code executor, a search engine, or any other external capability — and `MCPTool` turns each of its tools into an AntCrew `BaseTool` compatible with the existing ReAct tool-use loop.

---

## Quickstart

```python
from antcrew.tools.mcp import MCPTool, MCPToolset
from antcrew import ResearcherAgent, DevTeam
from antcrew.models.anthropic_model import AnthropicModel

# Wrap a single tool from an MCP server
search = MCPTool(
    server_url="http://localhost:8080",
    tool_name="web_search",
    description="Search the web for up-to-date information.",
)

agent = ResearcherAgent(llm=AnthropicModel(), tools=[search])
```

### Auto-discover all tools from a server

```python
# GET /tools/list → returns all available tools
tools = MCPToolset.from_server("http://localhost:8080")
# tools is a list[MCPTool] — pass it directly as agent tools
agent = ResearcherAgent(llm=AnthropicModel(), tools=tools)
```

---

## MCPTool reference

```python
MCPTool(
    server_url="http://localhost:8080",  # base URL of the MCP server
    tool_name="query_database",          # tool name as declared by the server
    description="Run a SQL query…",      # shown to the LLM in the schema
    timeout=30.0,                        # HTTP timeout in seconds (default 30)
    api_key="sk-...",                    # optional Bearer token
    input_schema={                       # optional JSON schema for the LLM
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
    },
)
```

`MCPTool.run(input)` accepts either a JSON string or plain text. The tool posts to `POST /tools/call` on the server and returns a `ToolResult`.

---

## MCPToolset reference

```python
tools = MCPToolset.from_server(
    "http://localhost:8080",
    timeout=30.0,       # optional
    api_key="sk-...",   # optional
)
```

Sends `GET /tools/list` to the server and creates one `MCPTool` per entry. The returned list can be passed directly to any agent that accepts `tools=`.

---

## MCP HTTP transport

AntCrew uses the **MCP HTTP transport** (not stdio). Your MCP server must expose:

| Endpoint | Method | Purpose |
|---|---|---|
| `/tools/list` | GET | Returns `{tools: [{name, description, inputSchema}]}` |
| `/tools/call` | POST | Accepts `{name, arguments}`, returns `{content, isError}` |

Most MCP servers support HTTP transport out of the box. For stdio-only servers, use a small bridge like [`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy).

---

## Authenticated servers

```python
tools = MCPToolset.from_server(
    "https://tools.example.com",
    api_key="Bearer sk-my-secret",
)
```

The key is sent as the `Authorization` header on every request.

---

## Example: wrapping a local filesystem MCP server

```bash
# Start a filesystem MCP server (example — any MCP server works)
uvx mcp-server-filesystem --port 8080 --root /tmp/workspace
```

```python
from antcrew.tools.mcp import MCPToolset
from antcrew import FeatureAgent

tools = MCPToolset.from_server("http://localhost:8080")
# e.g. tools = [MCPTool("read_file"), MCPTool("write_file"), MCPTool("list_dir")]

agent = FeatureAgent(llm=llm, tools=tools, project_dir="/tmp/workspace")
```

---

## Error handling

`MCPTool.run()` never raises — it returns a `ToolResult` with `error` set when the call fails or the server returns `isError: true`. The ReAct loop sees the error message and can retry or skip the tool.

```python
result = tool.run('{"sql": "SELECT * FROM users"}')
if not result.ok:
    print(result.error)   # "connection refused" or server error message
else:
    print(result.output)  # tool output as text
```

---

## Namespacing tools

When an agent uses tools from multiple MCP servers, namespacing prevents name collisions and makes the tool schema readable:

```python
from antcrew.tools.mcp import MCPToolset

# Tools appear to the LLM as "data/fetch_price", "data/list_symbols", etc.
data_tools = MCPToolset.from_server("http://data-svc:8080",   namespace="data")

# Tools appear as "exec/run_backtest", "exec/cancel_job", etc.
exec_tools = MCPToolset.from_server("http://exec-svc:8081",   namespace="exec")
```

The namespace prefix is applied to the tool name shown in LLM schemas. The underlying MCP call still uses the original (unprefixed) tool name, so the server needs no changes.

You can also apply a namespace to a single `MCPTool`:

```python
search = MCPTool(
    server_url="http://search-svc:8082",
    tool_name="web_search",
    description="Search the web.",
    namespace="search",
)
# search.name == "search/web_search"
```

---

## MCPRegistry — managing multiple servers

`MCPRegistry` groups toolsets by namespace and provides a clean interface for agents that consume tools from many servers:

```python
from antcrew.tools.mcp import MCPRegistry, MCPToolset

registry = (
    MCPRegistry()
    .register(MCPToolset.from_server("http://data-svc:8080",   namespace="data"))
    .register(MCPToolset.from_server("http://exec-svc:8081",   namespace="exec"))
    .register(MCPToolset.from_server("http://search-svc:8082", namespace="search"))
)

print(registry)
# MCPRegistry('data'(4), 'exec'(3), 'search'(2))

# Pass all tools to an agent
agent = ResearcherAgent(llm=llm, tools=registry.all_tools())

# Or pass only tools from one namespace
analyst = DataAnalystAgent(llm=llm, tools=registry.ns("data"))
```

### MCPRegistry API

| Method | Returns | Description |
|---|---|---|
| `.register(toolset, namespace="")` | `MCPRegistry` | Add a toolset; chainable |
| `.ns(namespace)` | `list[MCPTool]` | Tools from one namespace |
| `.all_tools()` | `list[MCPTool]` | All tools across all namespaces |
| `.namespaces()` | `list[str]` | Registered namespace keys |
| `len(registry)` | `int` | Total tool count |
