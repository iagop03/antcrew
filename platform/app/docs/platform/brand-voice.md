# Brand Voice Content Team (UC10)

`BrandVoiceContentTeam` combines `ContentTeam` with `ChromaMemory` to produce
content that consistently adheres to a brand's tone, style, and standards — and
improves over time as successful content examples are added to the memory store.

## Quick start

```python
from antcrew.teams.brand_voice_team import BrandVoiceContentTeam, BrandVoiceProfile

profile = BrandVoiceProfile(
    name="Acme Corp",
    tone="Professional but approachable",
    style="Short paragraphs, active voice, no jargon",
    persona="An industry expert who makes complex things simple",
    examples=[
        "Our API ships same-day — because waiting is so 2019.",
        "Built for developers, loved by teams.",
    ],
    standards=[
        "Always end with a clear call-to-action.",
        "Use 'you' not 'users' or 'customers'.",
        "Keep paragraphs under 4 sentences.",
    ],
)

team = BrandVoiceContentTeam(brand=profile)
result = team.run("Write a product launch announcement for our new API")
print(result.state["content_piece"].body)
```

### Installation

ChromaDB is required for semantic memory:

```bash
pip install antcrew[memory]
```

## How it works

```
BrandVoiceProfile
        │  to_context_block()
        ▼
  ChromaDB collection
  "bv_acme_corp"
        │
        ├─ brand_guidelines (seeded once)
        └─ brand_example × N (grows over time)
                │
                ▼
         semantic search on each run()
                │
                ▼
  augmented_request = original_request + brand_context + retrieved_examples
                │
                ▼
         ContentTeam.run(augmented_request)
```

On the first run the ChromaDB collection is seeded with the brand guidelines
and all examples from the profile.  Subsequent runs retrieve the most
semantically relevant examples for the current content request.

## Growing the brand voice library

After publishing a successful piece, add it to the memory store so future runs
can use it as a reference:

```python
team.add_example(
    "We just launched Real-Time Sync™ — your dashboard updates the moment "
    "your pipeline finishes, no refresh needed."
)
```

Search the library semantically:

```python
examples = team.search_examples("product launch announcement", n=3)
for ex in examples:
    print(ex.text)
```

## API reference

### `BrandVoiceProfile`

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Brand name (required) |
| `tone` | `str` | Tone description |
| `style` | `str` | Writing style notes |
| `persona` | `str` | Narrator persona |
| `examples` | `list[str]` | Example content in the brand voice |
| `standards` | `list[str]` | Mandatory content rules |

```python
profile.to_context_block()  # → formatted string injected into each run
```

### `BrandVoiceContentTeam`

```python
class BrandVoiceContentTeam:
    def __init__(
        self,
        brand: BrandVoiceProfile,
        *,
        model: Optional[BaseLLM] = None,
        memory_path: str = ".antcrew_brand_voice",
        trace_log: Optional[TraceLog] = None,
        max_retrieved_examples: int = 3,
    ): ...

    def run(self, request: str, *, thread_id: str = "default") -> RunResult: ...
    def add_example(self, content: str) -> str: ...
    def search_examples(self, query: str, *, n: int = 3) -> list[MemoryResult]: ...
```

**Notes:**
- Each brand gets an isolated ChromaDB collection (`bv_<normalized_brand_name>`).
- `memory_path` is the directory for the ChromaDB persistent store.
- `max_retrieved_examples` controls how many past examples are retrieved per run (default 3).
- `add_example()` returns the ChromaDB entry_id.

## Multi-brand agency setup

```python
from antcrew.teams.brand_voice_team import BrandVoiceContentTeam, BrandVoiceProfile

brands = {
    "TechStartup": BrandVoiceProfile(
        name="TechStartup",
        tone="Bold, direct, founder-voice",
        style="First-person plural, short sentences",
        standards=["Lead with the benefit, not the feature"],
    ),
    "LawFirmX": BrandVoiceProfile(
        name="LawFirmX",
        tone="Professional, authoritative, precise",
        style="Formal register, citations where relevant",
        standards=["Never give legal advice — describe capabilities only"],
    ),
}

teams = {name: BrandVoiceContentTeam(brand=profile, memory_path=f".brand_{name.lower()}")
         for name, profile in brands.items()}

result = teams["TechStartup"].run("Announce our Series A funding round")
```

## Using a shared TraceLog for observability

```python
from antcrew.trace import TraceLog

tlog = TraceLog("brand_voice.db", full_trace=True)
team = BrandVoiceContentTeam(brand=profile, trace_log=tlog)
result = team.run("Write our monthly newsletter intro")
```

## Related

- [ContentTeam reference](../engine/custom-agents.md)
- [Memory and context](../engine/index.md)
- [White-Label & Agency Billing](./white-label.md)
