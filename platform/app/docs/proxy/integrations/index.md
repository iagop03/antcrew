# keybridge integrations

keybridge is an OpenAI-compatible HTTP proxy — any framework or client that sends standard HTTP requests to LLM providers can use it without code changes beyond `base_url`.

## Supported frameworks

| Framework | Guide | Notes |
|-----------|-------|-------|
| **CrewAI** | [crewai.md](crewai.md) | Via `langchain_openai.ChatOpenAI` or `langchain_anthropic.ChatAnthropic` |
| **LangGraph / LangChain** | [langgraph.md](langgraph.md) | Drop-in `base_url` swap; tools and streaming unchanged |
| **AutoGen** | [autogen.md](autogen.md) | Via `config_list` + `base_url` |
| **OpenAI Agents SDK** | [autogen.md](autogen.md#openai-agents-sdk) | Via custom `AsyncOpenAI` client |
| **antcrew-platform** | [proxy index](../index.md) | Native integration with token management UI |

## Any OpenAI-compatible client

If your framework uses any OpenAI-compatible client, set `base_url` to keybridge's path for your provider:

```python
import openai

client = openai.OpenAI(
    base_url="https://keybridge.yourcompany.com/openai",
    api_key=os.environ["KEYBRIDGE_TOKEN"],
)
```

For Anthropic via the Anthropic SDK:

```python
import anthropic

client = anthropic.Anthropic(
    base_url="https://keybridge.yourcompany.com/anthropic",
    api_key=os.environ["KEYBRIDGE_TOKEN"],
)
```

## How the token works

You generate a UUID token and give it to keybridge as `PROXY_TOKEN`. Your application uses that token as the `api_key` in all requests. keybridge validates the token, strips it, and injects the real provider key before forwarding upstream.

The real provider keys never leave your keybridge container.

```
your app  →  POST /openai/v1/chat/completions  →  keybridge
                 Authorization: Bearer <uuid-token>    ↓ validates token
                                                  strips it, injects OPENAI_API_KEY
                                                       ↓
                                                  api.openai.com
```

## Generating a token without a platform

If you're not using a managed platform:

```bash
python -c "import uuid; print(uuid.uuid4())"
```

Use the output as both `PROXY_TOKEN` (keybridge env var) and `KEYBRIDGE_TOKEN` (application env var).
