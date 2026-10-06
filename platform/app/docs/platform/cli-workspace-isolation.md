# CLI Workspace Isolation

CLI drivers (claude-code, gemini, codex) store memory, history, and project context on disk in a **working directory**. Mapping each workspace to a distinct working directory gives you:

- **Memory sharing across tickets**: all runs in a workspace read from the same on-disk context
- **Isolation between workspaces**: different projects never bleed into each other
- **Concurrency safety**: concurrent calls to the same working directory are serialized (no race conditions)
- **Token usage tracking**: usage is attributed to the directory in `GET /usage`

---

## Configure a working directory

```bash
# Set the working directory for workspace 42
curl -X PATCH https://antcrew.org/workspaces/42/cli-working-dir \
  -H "X-Api-Key: acw_live_..." \
  -H "Content-Type: application/json" \
  -d '{"cli_working_dir": "/home/dev/projects/acme"}'

# Read it back
curl https://antcrew.org/workspaces/42/cli-working-dir \
  -H "X-Api-Key: acw_live_..."
```

**Response**:

```json
{
  "workspace_id": 42,
  "cli_working_dir": "/home/dev/projects/acme"
}
```

Pass `null` to clear the override:

```bash
curl -X PATCH https://antcrew.org/workspaces/42/cli-working-dir \
  -H "X-Api-Key: acw_live_..." \
  -H "Content-Type: application/json" \
  -d '{"cli_working_dir": null}'
```

---

## How it works

When `cli_working_dir` is set on a workspace, every run dispatched from that workspace passes `working_directory` deep into the request chain:

```
platform dispatch()
  → antcrew build_llm(extra_body={"working_directory": "/home/dev/projects/acme"})
    → AnthropicModel → every messages.create() call
      → KeyBridge (body pass-through)
        → remote-gateway reads MessagesRequest.working_directory
          → serializes concurrent calls via asyncio.Semaphore
          → passes --cwd to claude-code / gemini / codex subprocess
```

The path must exist on the machine running remote-gateway.

---

## Concurrency control

Remote-gateway maintains a semaphore pool keyed by `(driver, working_directory)`. Concurrent calls that target the same directory are queued — only one runs at a time (configurable).

| Environment variable | Default | Description |
|---|---|---|
| `CLAUDE_CODE_CONCURRENCY_PER_CWD` | `1` | Max concurrent claude-code calls per working directory |
| `GEMINI_CONCURRENCY_PER_CWD` | `1` | Max concurrent gemini calls per working directory |
| `CODEX_CONCURRENCY_PER_CWD` | `1` | Max concurrent codex calls per working directory |

Set to `0` to disable the limit (unlimited concurrency for that driver).

---

## Token usage

Remote-gateway records `working_directory` in every audit log entry. Query usage broken down by directory:

```bash
# All usage for a specific working directory
curl "https://gateway.internal:3001/usage?working_directory=/home/dev/projects/acme" \
  -H "Authorization: Bearer <gateway-token>"
```

**Response**:

```json
{
  "total_input_tokens": 48200,
  "total_output_tokens": 12400,
  "total_calls": 24,
  "by_driver": [
    { "driver": "claude-code", "input_tokens": 48200, "output_tokens": 12400, "calls": 24 }
  ]
}
```

Query parameters: `client_id`, `since` (ISO 8601), `until` (ISO 8601), `working_directory`.

---

## Token estimation for CLI drivers

Cloud drivers return exact token counts in `usage.input_tokens` / `usage.output_tokens`. CLI drivers may return zero — remote-gateway fills in an estimate using `len(text) // 4` for both input and output, and marks `"usage_estimated": true` in the audit entry. These estimates are conservative and suitable for billing projections but not exact metering.

---

## Using `local:` models in the SDK

Route a run through remote-gateway directly from Python using the `local:` model prefix:

```python
from antcrew import build_llm

llm = build_llm(
    "local:claude-code:default",
    base_url="http://localhost:3001",   # remote-gateway base URL
    api_key="<gateway-token>",
    extra_body={"working_directory": "/home/dev/projects/acme"},
)
```

The `local:` prefix tells the SDK to use `AnthropicModel` with the given proxy URL. The full model string is preserved so KeyBridge can route by driver prefix. `extra_body` is merged into every API call.

---

## RunResult.usage

When running via the antcrew SDK, the `RunResult` now includes a `usage` dict populated from `BaseLLM.get_usage_summary()`:

```python
result = team.run("Build JWT auth")
print(result.usage)
# {
#   "total_input_tokens": 24100,
#   "total_output_tokens": 6200,
#   "total_cost_usd": 0.048,
#   "by_agent": [...]
# }
```

Also included in `result.to_dict()` and `result.to_json()`.
