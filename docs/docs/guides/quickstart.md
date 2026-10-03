# Quick start

Get your first agent team running in 5 minutes. No cloud account. No API key required.

---

## Step 1 — Install

```bash
pip install antcrew
```

## Step 2 — Run your first pipeline

**Option A — Simulated LLM (instant, zero cost, no setup):**

```bash
antcrew run --model simulated "Build a REST API for user authentication"
```

This runs the full pipeline — BA, PM, backend dev, QA, reviewer — using a **deterministic simulated model** that produces the same artifacts every time. The content is fake (no real AI), but the artifact structure is real — useful for testing pipelines and CI, not for actual development output.

> No LLM is bundled with antcrew. The simulated model is a built-in test stub. For real AI output, use Option B (local Ollama) or Option C (cloud API key). If you want real AI with zero setup, use [antcrew-platform's managed tier](../platform/getting-started.md) — it provides the LLM.

**Option B — Fully local with Ollama (no API key, no data leaves your machine):**

```bash
# 1. Install Ollama from https://ollama.com, then:
ollama pull llama3

# 2. Run antcrew against it
antcrew run --model ollama:llama3 "Build a REST API for user authentication"
```

**Option C — Cloud model:**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
antcrew run --model claude "Build a REST API for user authentication"
```

## Step 3 — See what was produced

```bash
# View the run summary
antcrew inspect <run-id>

# Shows per-agent: prompt, response, tokens, cost, governance hash
```

The run ID is printed at the end of every `antcrew run`. The inspect output includes every agent's input and output — a full trace of the decision chain.

---

## Step 4 — From Python

```python
from antcrew import DevTeam
from antcrew.models import OllamaModel, AnthropicModel, SimulatedLLM

# Local — no API key
model = OllamaModel("llama3")
# Or: model = SimulatedLLM()
# Or: model = AnthropicModel("claude-sonnet-4-6")

team = DevTeam(model=model)
result = team.run("Build a REST API for user authentication")

# Typed artifacts — not dicts
prd = result.state["prd"]
print(prd.title)
print(prd.tech_stack)

tickets = result.state["tickets"]
for ticket in tickets:
    print(f"  [{ticket.priority}] {ticket.title}")

print(f"\nTotal cost: ${result.cost_usd:.4f}")   # 0.0 with Ollama
```

---

## Step 5 — Replay the trace

```bash
# Replay every agent call to detect model drift
antcrew trace replay <run-id>
```

Replay reruns all agent calls and compares outputs to the original. If a model update changed behavior, replay will tell you which agents diverged.

---

## What's next

- **Try other teams** — [FullStackTeam, ResearchTeam, ContentTeam, LegalReviewTeam](../engine/quick-agent.md)
- **Typed artifacts reference** — [Contracts & artifact types](../engine/contracts.md)
- **Add a human checkpoint** — [HITL local approvals](../engine/quick-agent.md#hitl)
- **Use 100+ LLM providers** — [Providers](../engine/providers.md)
- **Run in CI** — [EvalSuite regression testing](../engine/eval-suite.md)
- **Add semantic memory** — `pip install "antcrew[memory]"` then pass `ChromaMemory()` to any team
- **Governance hash** — cite agent configs in papers or pin in audit pipelines: `antcrew inspect <id>`

---

## Optional cloud layer

The `antcrew` SDK runs entirely locally — no cloud account needed. When your team needs shared runs, remote HITL, and a dashboard, deploy [antcrew-platform](../platform/getting-started.md).
