# ComparisonLLM — side-by-side model evaluation

`ComparisonLLM` runs a **primary** model and one or more **other** models concurrently on every call. The primary's response is returned to the agent unchanged; every model's output, cost, and latency are logged for post-run analysis.

This is different from `FallbackLLM` (which tries models sequentially on failure). `ComparisonLLM` always runs all models and never substitutes outputs — it observes, not fallbacks.

## Use cases

- Compare a faster/cheaper model against the current primary before switching
- Regression-test a prompt change across multiple LLMs simultaneously
- Collect ground-truth data for fine-tuning or eval datasets

## Quick start

```python
from antcrew_engine.models.comparison import ComparisonLLM
from antcrew_engine.models.anthropic_model import AnthropicModel
from antcrew_engine.models.ollama_model import OllamaModel
from antcrew_engine.config import build_llm

primary = AnthropicModel("claude-sonnet-4-6")
challenger = build_llm("openai:gpt-4o")
local = OllamaModel("llama3.2")

llm = ComparisonLLM(primary=primary, others=[challenger, local])

# Use as a normal LLM anywhere a BaseLLM is accepted
team = DevTeam(model=llm)
result = team.run("Build a JWT authentication module")

# After the run, inspect side-by-side results
for entry in llm.comparison_log():
    print(f"{entry['model']:30s}  cost={entry['cost_usd']:.4f}  latency={entry['duration_s']:.2f}s")
    print(entry["output"][:200])
    print()
```

## API reference

### `ComparisonLLM(primary, others)`

| Parameter | Type | Description |
|---|---|---|
| `primary` | `BaseLLM` | The LLM whose output is returned to the caller. Cost limits and streaming apply to this model only. |
| `others` | `list[BaseLLM]` | Models to run in parallel. Streaming is suppressed for these to avoid interleaving output. |

### `comparison_log() → list[dict]`

Returns all recorded comparison entries since the object was created. Each entry:

| Key | Type | Description |
|---|---|---|
| `model` | `str` | Model name / repr |
| `output` | `str` | Full completion text |
| `cost_usd` | `float` | Cost attributed to this model |
| `duration_s` | `float` | Wall-clock time for this call |
| `error` | `str \| None` | Exception message if the call failed; `None` on success |

Entries for failed calls (network error, timeout) still appear in the log with `error` set and `output: ""`. The primary's failure propagates normally to the caller.

### `get_usage_summary()` vs `full_usage_summary()`

- `get_usage_summary()` — primary model only. This is what cost limits and `AgentEvent` rows use.
- `full_usage_summary()` — aggregate across all models, including `others`. Useful for billing attribution in benchmark setups.

## ContextCompressor

`ContextCompressor` trims large file or log content before it enters a prompt, keeping costs and latency predictable.

```python
from antcrew_engine.context.compressor import TextSummaryCompressor, ASTCompressor

# For logs and plain text — keep first 30 + last 10 lines
compressor = TextSummaryCompressor()
result = compressor.compress(long_log, budget_tokens=1000)
print(result.method)           # "text_head_tail" or "passthrough"
print(result.compressed_tokens)

# For Python source — extract class/function signatures, drop bodies
ast_comp = ASTCompressor()
result = ast_comp.compress(python_source, budget_tokens=800, file_path="auth.py")
```

### Integration with `BaseLLM`

Pass `context_budget` to any `BaseLLM` subclass to enable automatic per-message compression before the API call:

```python
llm = AnthropicModel("claude-sonnet-4-6", context_budget=8000)
# Any message content > 8000 tokens is compressed before being sent
```

`context_budget` is advisory — the compressor selects the best strategy based on file extension (`.py` → AST-aware, everything else → head/tail text).

### `CompressedResult`

| Field | Type | Description |
|---|---|---|
| `text` | `str` | The compressed content |
| `original_tokens` | `int` | Estimated token count before compression |
| `compressed_tokens` | `int` | Estimated token count after |
| `method` | `str` | `"ast_python"`, `"text_head_tail"`, or `"passthrough"` (already within budget) |
