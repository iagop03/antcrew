# keybridge + LangGraph

LangGraph nodes call LLMs through LangChain model objects. Swapping the `base_url` to keybridge is the only change needed — graph structure, state, and tools remain identical.

## Setup

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

**2 — Create a keybridge-backed LLM:**

```python
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url=os.environ.get("KEYBRIDGE_URL", "http://localhost:8080") + "/openai",
    api_key=os.environ["KEYBRIDGE_TOKEN"],
    model="gpt-4o",
)
```

## Full agent graph example

```python
import os
from typing import Annotated, TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

KEYBRIDGE_URL = os.environ.get("KEYBRIDGE_URL", "http://localhost:8080")
KEYBRIDGE_TOKEN = os.environ["KEYBRIDGE_TOKEN"]

llm = ChatOpenAI(
    base_url=f"{KEYBRIDGE_URL}/openai",
    api_key=KEYBRIDGE_TOKEN,
    model="gpt-4o",
)

class State(TypedDict):
    messages: Annotated[list, add_messages]

def call_llm(state: State) -> State:
    response = llm.invoke(state["messages"])
    return {"messages": [response]}

graph = StateGraph(State)
graph.add_node("llm", call_llm)
graph.set_entry_point("llm")
graph.add_edge("llm", END)

app = graph.compile()

result = app.invoke({"messages": [HumanMessage(content="Explain BYOK in one sentence.")]})
print(result["messages"][-1].content)
```

## Multi-model graph (per-node routing)

Different nodes can use different providers — validator uses a cheap model, architect uses a premium one:

```python
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

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

def validate(state: State) -> State:
    # cheap: format checking, parsing
    response = cheap_llm.invoke(state["messages"])
    return {"messages": [response]}

def architect(state: State) -> State:
    # premium: complex reasoning
    response = premium_llm.invoke(state["messages"])
    return {"messages": [response]}
```

## With tools (function calling)

Tool calling works exactly as with direct API access — keybridge is transparent:

```python
from langchain_core.tools import tool

@tool
def search(query: str) -> str:
    """Search for information."""
    return f"Results for: {query}"

llm_with_tools = llm.bind_tools([search])

def agent_node(state: State) -> State:
    response = llm_with_tools.invoke(state["messages"])
    return {"messages": [response]}
```

## Streaming

LangGraph streaming passes through keybridge unchanged:

```python
for chunk in app.stream({"messages": [HumanMessage(content="Hello")]}):
    if "llm" in chunk:
        for msg in chunk["llm"]["messages"]:
            print(msg.content, end="", flush=True)
```

## ReAct agent with keybridge

```python
from langchain import hub
from langchain.agents import create_react_agent, AgentExecutor
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

llm = ChatOpenAI(
    base_url=f"{KEYBRIDGE_URL}/openai",
    api_key=KEYBRIDGE_TOKEN,
    model="gpt-4o",
)

@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression."""
    return str(eval(expression))

prompt = hub.pull("hwchase17/react")
agent = create_react_agent(llm, [calculator], prompt)
executor = AgentExecutor(agent=agent, tools=[calculator], verbose=True)
executor.invoke({"input": "What is 2 ** 32?"})
```

## Production tips

- Set `KEYBRIDGE_URL` from an environment variable — never hardcode it
- Use `FAILOVER_CHAIN` to avoid single-provider outages in long-running graphs
- Mount `AUDIT_LOG_PATH` to a persistent volume to retain the per-request audit trail
- Run keybridge behind a TLS terminator (Caddy, nginx, Fly.io) in production
