# Custom capabilities

The engine ships with built-in capabilities (Architect, TaskPlanner, CodeGenerator, TestGenerator, TestRunner…). When none of them fit, you can write your own by subclassing `BaseExecutor`.

## Writing a custom capability

A capability has two responsibilities: decide when it should run (`can_handle`), and do the work (`execute`).

```python
from antcrew_engine.capabilities import BaseExecutor
from antcrew_engine.artifacts import ArtifactStore, ArtifactKind
from antcrew_engine.goals import Goal, ConditionId
from antcrew_engine.config import build_llm

class WebResearcher(BaseExecutor):
    """Searches the web and stores a research_report artifact."""

    name = "WebResearcher"

    def __init__(self, llm, search_fn):
        self.llm = llm
        self.search_fn = search_fn  # callable(query: str) -> list[str]

    def can_handle(self, goal: Goal, store: ArtifactStore) -> bool:
        # Run when there's no research report yet
        return not store.get("research_report")

    def execute(self, goal: Goal, store: ArtifactStore) -> None:
        snippets = self.search_fn(goal.description)
        context = "\n\n".join(snippets)

        response = self.llm.complete(
            system="You are a research analyst. Write a concise report.",
            user=f"Goal: {goal.description}\n\nSources:\n{context}",
        )

        store.put("research_report", {
            "kind": ArtifactKind.DOCUMENT,
            "content": response.text,
            "sources": snippets,
        })
```

Register it like any other capability:

```python
from antcrew_engine import EngineLoop, CapabilityRegistry, MemoryStore, artifact_validators
from antcrew_engine.config import build_llm

llm = build_llm("anthropic:claude-opus-5")

registry = CapabilityRegistry()
registry.register(WebResearcher(llm, search_fn=my_search))
registry.register(...)   # add other capabilities

store = MemoryStore()
engine = EngineLoop(registry, artifact_validators)
engine.run(store, goal)
```

## Adding tools to a capability

Capabilities don't use a `@tool` decorator — they're plain Python. Pass any callable as a dependency and call it directly inside `execute`:

```python
import httpx

def fetch_url(url: str) -> str:
    return httpx.get(url, timeout=10).text

class PageScraper(BaseExecutor):
    name = "PageScraper"

    def __init__(self, llm):
        self.llm = llm

    def can_handle(self, goal, store):
        return not store.get("scraped_content")

    def execute(self, goal, store):
        url = store.get("target_url")["value"]
        html = fetch_url(url)          # plain function call
        summary = self.llm.complete(
            system="Extract main content from HTML.",
            user=html[:8000],
        )
        store.put("scraped_content", {"kind": ArtifactKind.DOCUMENT, "content": summary.text})
```

## HITL inside a custom capability

To pause for human input, use `HitlReviewer` as a separate capability that runs after yours. Register it in the `CapabilityRegistry` with `after_capability` set to your capability's name:

```python
from antcrew_engine import HitlReviewer

registry.register(WebResearcher(llm, search_fn=my_search))
registry.register(HitlReviewer(
    platform_url="https://antcrew.org",
    api_key="acw_live_...",
    after_capability="WebResearcher",
    prompt="Review the research report before the plan is created.",
))
registry.register(TaskPlanner(llm))
```

The engine will call `WebResearcher`, then pause at `HitlReviewer` until someone approves in the platform dashboard, then continue with `TaskPlanner`.

## Custom artifact validators

By default the engine uses the built-in `artifact_validators` dict. To add your own validation conditions:

```python
from antcrew_engine import artifact_validators, ConditionId

my_validators = {
    **artifact_validators,
    ConditionId("research_complete"): lambda store: bool(store.get("research_report")),
}

engine = EngineLoop(registry, my_validators)
```

See [Typed artifacts](contracts.md) for the full `ArtifactStore` API and built-in artifact kinds.
