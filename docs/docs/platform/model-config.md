# Model configuration

antcrew lets you control which LLM each agent uses at four levels of specificity, applied in this order at run time:

```
run.model_overrides[agent]          ← highest priority (per-run, per-agent)
  workspace.agent_models[agent]     ← workspace default for that agent
    workspace.agent_models["default"]  ← workspace-wide fallback
      platform.default_agent_models["default"]  ← platform-wide admin default
        "claude"                    ← lowest priority (hard-coded fallback)
```

## Platform-level default (admin only)

Platform admins can set the default model that applies across all workspaces that have no workspace-level override. This is useful for switching the entire platform from `claude` to a cheaper model without touching every workspace.

Configured in the **Admin → Platform defaults** section of the admin panel, or via API:

```bash
# Read current platform default
GET /admin/agent-models/defaults
Authorization: Bearer <admin-session>

# Set a new platform default
curl -X PATCH https://antcrew.org/admin/agent-models/defaults \
  -H "Content-Type: application/json" \
  -d '{"default_agent_models": {"default": "groq:llama-3.1-70b-versatile"}}'
```

Workspaces that have an explicit `workspace.agent_models["default"]` are not affected. The platform default only fills the gap when the workspace has no preference.

Workspace members can read the current platform default from `GET /engine/platform-defaults` (API-key accessible, no admin required) — this is what the Settings model matrix "plataforma" row shows.

## Workspace-level defaults

Set in **Settings → Agent models**. Applies to every run in the workspace unless overridden.

```http
PATCH /workspaces/{id}/agent-models
X-Api-Key: acw_...

{
  "agent_models": {
    "default": "groq:llama-3.3-70b-versatile",
    "BackendDevAgent": "claude:claude-sonnet-5"
  }
}
```

Use the key `"default"` to set a fallback for all agents not explicitly listed. Pass `null` to clear all workspace overrides and fall back to the platform default (`claude`).

```bash
# Full example: fast cheap model for most agents, stronger for backend
curl -X PATCH https://antcrew.org/workspaces/42/agent-models \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{
    "agent_models": {
      "default": "groq:llama-3.3-70b-versatile",
      "BackendDevAgent": "claude:claude-sonnet-5",
      "ReviewerAgent": "claude:claude-opus-5"
    }
  }'
```

## Per-run overrides

Passed directly in the run request. Applies only to that run — does not affect workspace defaults.

```bash
curl -X POST https://antcrew.org/run/ \
  -H "X-Api-Key: acw_..." \
  -H "Content-Type: application/json" \
  -d '{
    "team": "DevTeam",
    "request": "Build an auth module",
    "model_overrides": {
      "BackendDevAgent": "deepseek:deepseek-chat"
    }
  }'
```

`model` (string) sets the run-level default — equivalent to `"default"` in `model_overrides` but for a single run:

```json
{ "team": "DevTeam", "request": "...", "model": "groq:llama-3.3-70b-versatile" }
```

## Run presets

A **preset** is a named `{team, model_overrides}` configuration saved per workspace. Use presets to switch between model configurations quickly from the dashboard without re-specifying overrides each time.

### Save a preset via API

```http
POST /workspaces/{id}/presets
X-Api-Key: acw_...

{
  "name": "Groq sprint",
  "team": "DevTeam",
  "model_overrides": {
    "default": "groq:llama-3.3-70b-versatile",
    "BackendDevAgent": "claude:claude-sonnet-5"
  }
}
```

### List and delete

```bash
# List all presets for a workspace
GET /workspaces/{id}/presets

# Filter by team
GET /workspaces/{id}/presets?team=DevTeam

# Delete
DELETE /workspaces/{id}/presets/{preset_id}
```

### Using presets from the dashboard

1. Open **Runs → New Run**.
2. Select the team. Saved presets for that team appear as chips below the selector.
3. Click a preset chip to apply its model overrides.
4. Expand **Configurar modelos por agente** to inspect or adjust individual agent assignments before launching.
5. Save the current configuration as a new preset using the input at the bottom of the panel.

## Supported model strings

All model strings follow the `provider:model-id` format understood by the antcrew engine. Common values:

| String | Provider |
|---|---|
| `claude:claude-sonnet-5` | Anthropic Claude Sonnet 5 |
| `claude:claude-opus-5` | Anthropic Claude Opus 5 |
| `claude:claude-haiku-4-5-20251001` | Anthropic Claude Haiku 4.5 |
| `gpt-4o` | OpenAI GPT-4o |
| `deepseek:deepseek-chat` | DeepSeek Chat |
| `deepseek:deepseek-reasoner` | DeepSeek R1 |
| `groq:llama-3.3-70b-versatile` | Llama 3.3 70B via Groq |
| `gemini:gemini-2.0-flash` | Google Gemini 2.0 Flash |
| `mistral:mistral-large-latest` | Mistral Large |
| `xai:grok-2` | xAI Grok-2 |
| `simulated` | Deterministic mock — for testing |

See [LLM providers](../engine/providers.md) for the full list and BYOK / Proxy setup.

## Which agents are in each team?

```bash
GET /run/teams/{team}/agents
```

Returns the ordered list of agents for a team — name, description, artifact type, and position. Use this to know which agent names are valid in `model_overrides`.

```json
{
  "team": "DevTeam",
  "agents": [
    {"name": "BusinessAnalystAgent", "description": "...", "position": 0},
    {"name": "PMAgent",              "description": "...", "position": 1},
    {"name": "BackendDevAgent",      "description": "...", "position": 2}
  ]
}
```
