# A2A Protocol

AntCrew exposes every registered team as an **A2A-compatible agent** — allowing external orchestrators (Google ADK, other A2A frameworks) to discover and invoke AntCrew teams through a standard REST interface without any custom integration code.

A2A (Agent-to-Agent) is an open interoperability protocol using JSON-RPC 2.0 over HTTP with optional SSE streaming.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/a2a/{team}` | AgentCard — discovery descriptor |
| `POST` | `/a2a/{team}` | JSON-RPC 2.0 endpoint (tasks/send, tasks/get, tasks/cancel) |
| `GET` | `/a2a/{team}/runs/{run_id}` | Poll task status |

Available teams: `DevTeam`, `FullStackTeam`, `ResearchTeam`, `ContentTeam`, plus any registered via `ANTCREW_TEAMS`.

## AgentCard

```bash
curl https://antcrew.org/a2a/DevTeam
```

```json
{
  "name": "DevTeam",
  "description": "AntCrew DevTeam — AI agent pipeline.",
  "url": "https://antcrew.org/a2a/DevTeam",
  "version": "1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "skills": [
    { "id": "run", "name": "Run team", "description": "Execute the DevTeam pipeline" }
  ]
}
```

## Sending a task

```bash
curl -X POST https://antcrew.org/a2a/DevTeam \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-1",
    "method": "tasks/send",
    "params": {
      "id": "task-abc123",
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Build a REST API for a todo app"}]
      }
    }
  }'
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "result": {
    "id": "run_4f3c2e1d...",
    "status": { "state": "submitted" },
    "artifacts": [],
    "metadata": {
      "antcrew_run_id": "run_4f3c2e1d...",
      "team": "DevTeam",
      "stream_url": "https://antcrew.org/stream/run_4f3c2e1d..."
    }
  }
}
```

## Task states

| A2A state | AntCrew status |
|-----------|---------------|
| `submitted` | Initial (dispatched, pipeline.start pending) |
| `working` | `running` |
| `completed` | `success` |
  | `failed` | `error` |
| `canceled` | `cancelled` |

## Polling task status

```bash
curl https://antcrew.org/a2a/DevTeam/runs/run_4f3c2e1d \
  -H "X-Api-Key: acw_..."
```

When completed, the response includes `artifacts` with the result text extracted from the run state.

## Calling from Google ADK

```python
from google.adk.tools.agent_tool import AgentTool

antcrew_tool = AgentTool(
    agent_url="https://antcrew.org/a2a/DevTeam",
    headers={"X-Api-Key": "acw_..."},
)
```

## Security

The A2A endpoints require an `X-Api-Key` header — the same API keys used by the rest of the platform. CSRF is not required (A2A is a server-to-server protocol).

No CORS restrictions apply to `/a2a/*` — external agents make cross-origin requests by design.
