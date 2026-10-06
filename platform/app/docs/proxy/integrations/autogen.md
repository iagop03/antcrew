# keybridge + AutoGen / OpenAI Agents SDK

Both AutoGen and the OpenAI Agents SDK accept a custom `base_url`, so keybridge integrates without any framework changes.

## AutoGen

### Setup

**1 — Run keybridge:**

```bash
docker run -d \
  --name keybridge \
  -p 8080:8080 \
  -e PROXY_TOKEN=your-uuid-token \
  -e OPENAI_API_KEY=sk-proj-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  ghcr.io/iagop03/keybridge:latest
```

**2 — Point AutoGen at keybridge:**

```python
import os
import autogen

KEYBRIDGE_URL = os.environ.get("KEYBRIDGE_URL", "http://localhost:8080")
KEYBRIDGE_TOKEN = os.environ["KEYBRIDGE_TOKEN"]

config_list = [{
    "model": "gpt-4o",
    "base_url": f"{KEYBRIDGE_URL}/openai/v1",
    "api_key": KEYBRIDGE_TOKEN,
    "api_type": "openai",
}]

llm_config = {"config_list": config_list, "cache_seed": None}

assistant = autogen.AssistantAgent(
    name="assistant",
    llm_config=llm_config,
    system_message="You are a helpful AI assistant.",
)

user_proxy = autogen.UserProxyAgent(
    name="user",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=5,
    code_execution_config=False,
)

user_proxy.initiate_chat(
    assistant,
    message="Summarize the benefits of BYOK for enterprise AI deployments.",
)
```

### Multi-agent with multiple providers

```python
# Cheap model for simple agents
cheap_config = [{
    "model": "llama-3.3-70b-versatile",
    "base_url": f"{KEYBRIDGE_URL}/groq/openai/v1",
    "api_key": KEYBRIDGE_TOKEN,
}]

# Premium model for complex reasoning
premium_config = [{
    "model": "gpt-4o",
    "base_url": f"{KEYBRIDGE_URL}/openai/v1",
    "api_key": KEYBRIDGE_TOKEN,
}]

researcher = autogen.AssistantAgent(
    name="researcher",
    llm_config={"config_list": premium_config},
    system_message="Research specialist. Provide accurate, well-cited answers.",
)

summarizer = autogen.AssistantAgent(
    name="summarizer",
    llm_config={"config_list": cheap_config},
    system_message="Summarizer. Condense long content into bullet points.",
)
```

### AutoGen with Claude (Anthropic)

AutoGen's OpenAI-compatible client works with the `/openai` path of keybridge. For Anthropic models exposed via OpenAI-compatible API:

```python
# DeepSeek, Groq, or any OpenAI-compatible Anthropic-hosted endpoint
config_list = [{
    "model": "claude-3-5-sonnet-20241022",
    "base_url": f"{KEYBRIDGE_URL}/openai/v1",
    "api_key": KEYBRIDGE_TOKEN,
}]
```

---

## OpenAI Agents SDK

The OpenAI Agents SDK (`openai-agents`) accepts a custom `AsyncOpenAI` client.

### Setup

```bash
pip install openai-agents
```

```python
import os
import asyncio
from openai import AsyncOpenAI
from agents import Agent, Runner

KEYBRIDGE_URL = os.environ.get("KEYBRIDGE_URL", "http://localhost:8080")
KEYBRIDGE_TOKEN = os.environ["KEYBRIDGE_TOKEN"]

client = AsyncOpenAI(
    base_url=f"{KEYBRIDGE_URL}/openai",
    api_key=KEYBRIDGE_TOKEN,
)

agent = Agent(
    name="assistant",
    model="gpt-4o",
    client=client,
    instructions="You are a helpful assistant.",
)

async def main():
    result = await Runner.run(agent, "Explain why API key isolation matters.")
    print(result.final_output)

asyncio.run(main())
```

### Multi-agent handoffs

```python
from agents import Agent, Runner, handoff

cheap_client = AsyncOpenAI(
    base_url=f"{KEYBRIDGE_URL}/groq",
    api_key=KEYBRIDGE_TOKEN,
)

premium_client = AsyncOpenAI(
    base_url=f"{KEYBRIDGE_URL}/openai",
    api_key=KEYBRIDGE_TOKEN,
)

triage_agent = Agent(
    name="triage",
    model="llama-3.3-70b-versatile",
    client=cheap_client,
    instructions="Classify requests and hand off to the right specialist.",
)

legal_agent = Agent(
    name="legal_specialist",
    model="gpt-4o",
    client=premium_client,
    instructions="Expert legal analysis. Cite relevant regulations.",
)

triage_agent = triage_agent.clone(
    handoffs=[handoff(legal_agent)]
)

async def main():
    result = await Runner.run(triage_agent, "Review this contract for GDPR compliance.")
    print(result.final_output)
```

### Tool use

```python
from agents import Agent, Runner, function_tool

@function_tool
def get_current_price(ticker: str) -> str:
    """Fetch the current price for a stock ticker."""
    # your implementation
    return f"${ticker}: $123.45"

agent = Agent(
    name="financial_agent",
    model="gpt-4o",
    client=client,
    tools=[get_current_price],
)
```

---

## Failover for both frameworks

Both AutoGen and Agents SDK are unaware of provider failures — keybridge handles them transparently. Enable failover at the proxy level:

```bash
docker run -d \
  -e PROXY_TOKEN=your-token \
  -e OPENAI_API_KEY=sk-proj-... \
  -e GROQ_API_KEY=gsk_... \
  -e FAILOVER_CHAIN=openai:gpt-4o,groq:llama-3.3-70b-versatile \
  ghcr.io/iagop03/keybridge:latest
```

Both frameworks call `POST /v1/chat/completions` — keybridge tries OpenAI first, falls back to Groq on any error.

---

## What keybridge does not change

- Tool definitions and function calling schemas
- Agent handoff logic
- Conversation history / memory
- Streaming behavior
- Token counting

Everything above is handled by the framework. keybridge only changes which upstream API key is used and where the request is forwarded.
