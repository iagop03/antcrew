# LiteLLM — 100+ providers with one prefix

`LiteLLMModel` is an optional backend that routes to any provider supported by [LiteLLM](https://github.com/BerriAI/litellm): Bedrock, Vertex AI, Together AI, Fireworks, Perplexity, Cohere, Replicate, and dozens more — all via the same `complete()` interface as every other AntCrew model.

```bash
pip install antcrew[litellm]
```

---

## Quickstart

```python
from antcrew.models.litellm_model import LiteLLMModel

# AWS Bedrock
llm = LiteLLMModel("bedrock/claude-3-5-sonnet-20241022")

# Together AI
llm = LiteLLMModel("together_ai/meta-llama/Llama-3-70b-chat-hf")

# Vertex AI
llm = LiteLLMModel("vertex_ai/gemini-2.0-flash")

# Perplexity
llm = LiteLLMModel("perplexity/llama-3.1-sonar-large-128k-online")
```

Use it anywhere a `BaseLLM` is accepted:

```python
from antcrew import DevTeam

team = DevTeam(model=llm)
result = team.run("Build a rate limiter middleware")
```

---

## YAML config

Add the `litellm:` prefix to any model string in your `agentteam.yaml`:

```yaml
team: dev
model: litellm:bedrock/claude-3-5-sonnet-20241022
```

Per-agent override:

```yaml
team: dev
model: claude                        # default for most agents
agents:
  backend_dev:
    model: litellm:together_ai/meta-llama/Llama-3-70b-chat-hf
```

---

## Provider examples

| Provider | Model string |
|---|---|
| AWS Bedrock | `litellm:bedrock/claude-3-5-sonnet-20241022` |
| Google Vertex AI | `litellm:vertex_ai/gemini-2.0-flash` |
| Together AI | `litellm:together_ai/meta-llama/Llama-3-70b-chat-hf` |
| Fireworks AI | `litellm:fireworks_ai/accounts/fireworks/models/llama-v3p1-70b-instruct` |
| Perplexity | `litellm:perplexity/llama-3.1-sonar-large-128k-online` |
| Cohere | `litellm:cohere/command-r-plus` |
| Replicate | `litellm:replicate/meta/meta-llama-3-70b-instruct` |
| Ollama (local) | `litellm:ollama/llama3.2` |
| OpenAI-compat API | `litellm:openai/gpt-4o` |

See the full list at [docs.litellm.ai/docs/providers](https://docs.litellm.ai/docs/providers).

---

## Credentials

LiteLLMModel reads credentials from the same environment variables LiteLLM uses:

| Provider | Env var |
|---|---|
| AWS Bedrock | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_REGION_NAME` |
| Google Vertex AI | `GOOGLE_APPLICATION_CREDENTIALS` or ADC |
| Together AI | `TOGETHERAI_API_KEY` |
| Fireworks AI | `FIREWORKS_AI_API_KEY` |
| Perplexity | `PERPLEXITYAI_API_KEY` |
| Cohere | `COHERE_API_KEY` |

Pass them inline if preferred:

```python
llm = LiteLLMModel(
    "bedrock/claude-3-5-sonnet-20241022",
    aws_region_name="eu-west-1",  # extra kwarg forwarded to litellm
)

llm = LiteLLMModel(
    "openai/gpt-4o",
    api_key="sk-...",
    api_base="https://your-proxy/v1",
)
```

---

## Streaming

Token streaming works transparently. Set `on_token` on the model before using it in a team:

```python
llm = LiteLLMModel("together_ai/meta-llama/Llama-3-70b-chat-hf")
llm.on_token = lambda tok: print(tok, end="", flush=True)
```

---

## When to use LiteLLMModel vs native adapters

| Scenario | Recommendation |
|---|---|
| Primary model (Claude, OpenAI, Groq, Gemini) | Use native adapters — lower latency, tighter error handling |
| AWS Bedrock, Vertex AI, Together AI, Fireworks | Use `LiteLLMModel` — native adapters don't exist yet |
| Experimenting with an obscure provider | Use `LiteLLMModel` — instant support without new code |
| Production, performance-critical path | Prefer native adapters when available |

---

## JSON mode

LiteLLMModel sets `response_format: {"type": "json_object"}` automatically when the agent calls `system_parsed()` or `system_structured()`.  Not all providers support JSON mode — if yours doesn't, LiteLLM falls back to prompt-level instruction.
