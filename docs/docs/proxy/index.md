# keybridge — proxy overview

keybridge is a self-hosted LLM key proxy. It sits between your AI workloads and LLM providers so your API keys never leave your infrastructure.

```
your agent / platform  →  keybridge (yours)  →  api.anthropic.com
        ↑                       ↑                  api.openai.com
sends a UUID token         holds your real          api.groq.com
  (not the real key)       provider keys              …
```

It works with any framework that makes HTTP requests to LLMs: **CrewAI, LangGraph, AutoGen, OpenAI Agents SDK, antcrew**, or a plain `openai` client.

## Why use keybridge?

- **Zero key exposure** — provider keys live in your keybridge container, not in your application code or any cloud platform
- **Drop-in replacement** — change `base_url` only; no other code changes required
- **15 providers** — Anthropic, OpenAI, Azure OpenAI, Groq, Gemini, Moonshot, DeepSeek, Mistral, xAI, Together, Fireworks, Cerebras, Ollama, LM Studio, vLLM
- **Automatic failover** — `POST /v1/chat/completions` tries providers in order; recovers from timeouts and rate-limits
- **Tamper-evident audit log** — per-request JSONL with SHA-256 hash chain; exportable via `GET /audit/export` and verifiable via `GET /audit/verify`
- **Live metrics** — request count, p50/p99 latency, token totals, and error breakdown at `GET /metrics`
- **Multi-key round-robin** — distribute load across multiple API keys per provider
- **Multi-token hot rotation** — rotate tokens without downtime
- **mTLS support** — run token auth, mTLS, or both for defense-in-depth
- **Per-token rate limiting** — configurable sliding-window RPM limit per token (`PROXY_TOKEN_RPM`)
- **OpenTelemetry tracing** — optional OTLP export with `pip install keybridge[otel]`

## Quick start

```bash
# 1 — generate a token
KEYBRIDGE_TOKEN=$(python -c "import uuid; print(uuid.uuid4())")

# 2 — run keybridge
docker run -d \
  --name keybridge -p 8080:8080 \
  -e PROXY_TOKEN=$KEYBRIDGE_TOKEN \
  -e OPENAI_API_KEY=sk-proj-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  ghcr.io/iagop03/keybridge:latest

# 3 — call it from any OpenAI-compatible client
python -c "
import openai, os
client = openai.OpenAI(
    base_url='http://localhost:8080/openai',
    api_key='$KEYBRIDGE_TOKEN',
)
print(client.chat.completions.create(model='gpt-4o', messages=[{'role':'user','content':'Hello'}]).choices[0].message.content)
"
```

## Endpoints

| Endpoint | Description |
|---|---|
| `POST /{provider}/{path}` | Route to a specific provider |
| `POST /v1/chat/completions` | Unified with automatic failover via `FAILOVER_CHAIN` |
| `GET /health` | Liveness check + provider key summary |
| `GET /metrics` | Live in-memory stats (p50/p99 latency, tokens, errors) |

## Integrations

- [CrewAI](integrations/crewai.md)
- [LangGraph / LangChain](integrations/langgraph.md)
- [AutoGen / OpenAI Agents SDK](integrations/autogen.md)
- [All integrations](integrations/index.md)

## Reference

- [Provider routing](routing.md) — full path-prefix table, upstream URLs
- [Configuration](configuration.md) — all environment variables

## Source

[github.com/iagop03/keybridge](https://github.com/iagop03/keybridge) — MIT license
