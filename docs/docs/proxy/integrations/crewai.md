# keybridge + CrewAI

keybridge acts as an OpenAI-compatible drop-in that CrewAI agents talk to. Your API keys stay in your keybridge container; CrewAI only ever sees the proxy token.

## Setup

**1 — Run keybridge:**

```bash
docker run -d \
  --name keybridge \
  -p 8080:8080 \
  -e PROXY_TOKEN=your-uuid-token \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e OPENAI_API_KEY=sk-proj-... \
  -e GROQ_API_KEY=gsk_... \
  ghcr.io/iagop03/keybridge:latest
```

**2 — Configure CrewAI agents to use keybridge:**

```python
import os
from crewai import Agent, Task, Crew
from langchain_openai import ChatOpenAI

KEYBRIDGE_URL = os.environ.get("KEYBRIDGE_URL", "http://localhost:8080")
KEYBRIDGE_TOKEN = os.environ["KEYBRIDGE_TOKEN"]

# Standard agent — OpenAI-compatible path
llm = ChatOpenAI(
    base_url=f"{KEYBRIDGE_URL}/openai",
    api_key=KEYBRIDGE_TOKEN,
    model="gpt-4o",
)

researcher = Agent(
    role="Senior Researcher",
    goal="Find and synthesize information on a given topic",
    backstory="Expert analyst with a talent for spotting patterns",
    llm=llm,
)

writer = Agent(
    role="Content Writer",
    goal="Write clear, concise reports",
    backstory="Expert technical writer",
    llm=llm,
)

task1 = Task(
    description="Research the latest developments in {topic}",
    expected_output="A bullet-point summary of key findings",
    agent=researcher,
)

task2 = Task(
    description="Turn the research into a 3-paragraph report",
    expected_output="A well-structured report",
    agent=writer,
)

crew = Crew(agents=[researcher, writer], tasks=[task1, task2], verbose=True)
result = crew.kickoff(inputs={"topic": "LLM cost optimization"})
```

## Using Claude (Anthropic) via keybridge

CrewAI supports Anthropic models via `langchain_anthropic`:

```python
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(
    anthropic_api_url=f"{KEYBRIDGE_URL}/anthropic",
    api_key=KEYBRIDGE_TOKEN,
    model="claude-opus-5",
)

agent = Agent(
    role="Legal Reviewer",
    goal="Review contracts for compliance risks",
    backstory="Senior counsel with 15 years of experience",
    llm=llm,
)
```

## Per-agent model routing

Use different models for different agent roles — cheap for formatting, premium for reasoning:

```python
cheap_llm = ChatOpenAI(
    base_url=f"{KEYBRIDGE_URL}/groq",
    api_key=KEYBRIDGE_TOKEN,
    model="llama-3.3-70b-versatile",
)

premium_llm = ChatAnthropic(
    anthropic_api_url=f"{KEYBRIDGE_URL}/anthropic",
    api_key=KEYBRIDGE_TOKEN,
    model="claude-opus-5",
)

# Simple validator — uses cheap model
validator = Agent(role="Validator", llm=cheap_llm, ...)

# Complex architect — uses premium model
architect = Agent(role="Solution Architect", llm=premium_llm, ...)
```

## Failover across providers

Configure keybridge with `FAILOVER_CHAIN` and use the unified endpoint:

```bash
docker run -d \
  -e PROXY_TOKEN=your-token \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e OPENAI_API_KEY=sk-proj-... \
  -e GROQ_API_KEY=gsk_... \
  -e FAILOVER_CHAIN=anthropic:claude-opus-5,openai:gpt-4o,groq:llama-3.3-70b-versatile \
  ghcr.io/iagop03/keybridge:latest
```

```python
llm = ChatOpenAI(
    base_url=f"{KEYBRIDGE_URL}",  # /v1 unified endpoint
    api_key=KEYBRIDGE_TOKEN,
    model="any",  # model is ignored — FAILOVER_CHAIN controls selection
)
```

If Anthropic is down or rate-limited, keybridge automatically tries OpenAI, then Groq. CrewAI sees no error.

## Environment variables for production

```bash
# .env
KEYBRIDGE_URL=https://keybridge.yourcompany.com
KEYBRIDGE_TOKEN=3f8a1b2c-4d5e-6f7a-8b9c-0d1e2f3a4b5c
```

```python
from dotenv import load_dotenv
load_dotenv()

KEYBRIDGE_URL = os.environ["KEYBRIDGE_URL"]
KEYBRIDGE_TOKEN = os.environ["KEYBRIDGE_TOKEN"]
```

## Audit log

Every request keybridge handles is written to `AUDIT_LOG_PATH` (JSONL). Each entry includes provider, model, status, latency, and token counts — with API keys hashed. Use this for cost tracking per agent type.

```bash
# Sample audit entry
{"ts": "2026-08-23T10:00:00Z", "provider": "openai", "model": "gpt-4o", "status": 200, "tokens_in": 512, "tokens_out": 128, "latency_ms": 840}
```
