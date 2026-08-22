# antcrew

[![CI](https://github.com/iagop03/antcrew/actions/workflows/ci.yml/badge.svg)](https://github.com/iagop03/antcrew/actions)
[![PyPI](https://img.shields.io/pypi/v/antcrew)](https://pypi.org/project/antcrew/)
[![Python](https://img.shields.io/pypi/pyversions/antcrew)](https://pypi.org/project/antcrew/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Multi-agent framework for Python. Typed outputs. Full trace. Works offline.**

Three lines to your first agent team, no API key required:

```python
from antcrew import QuickStart

result = QuickStart.dev().run("Build a FastAPI auth service")
print(result.state["prd"].title)           # typed PRD artifact
print(result.state["code_artifacts"])      # typed code files
```

Or from the CLI, fully local with Ollama:

```bash
pip install antcrew
antcrew run "Build a FastAPI auth service" --model ollama:llama3
```

---

## Why antcrew

| | antcrew | CrewAI | MetaGPT |
|---|---|---|---|
| Typed output contracts | ✓ Pydantic artifacts | ✗ dict | partial |
| Trace & replay any run | ✓ SQLite TraceLog | ✗ | ✓ |
| Works 100% offline | ✓ Ollama natively | partial | partial |
| Lines to first agent | **3** | ~15 | ~20 |
| Governance hash per agent | ✓ SHA-256 | ✗ | ✗ |
| CLI (commands) | ✓ 29 commands | limited | basic |
| Production SaaS layer | ✓ optional | ✗ | ✗ |

The core differentiator: every output is a **typed artifact** (Pydantic class, not a dict) and every decision is recorded to a **local TraceLog** you can replay. Both work offline, for free.

---

## Quick start

**Zero setup — simulated LLM, no credentials:**

Runs immediately. Produces typed artifacts with deterministic fake content — good for testing and CI, not for real AI output.

```bash
pip install antcrew
antcrew run --model simulated "Build a REST API for user authentication"
```

**Fully local — Ollama (real AI, no API key, no data leaves your machine):**

Requires [Ollama](https://ollama.com) installed (~5 min) and `ollama pull llama3`.

```bash
antcrew run --model ollama:llama3 "Build a REST API for user authentication"
```

**Cloud model — real AI, no local setup:**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
antcrew run --model claude "Build a REST API for user authentication"
```

> **Don't want to configure anything?** Use [antcrew-platform](https://github.com/iagop03/antcrew-platform) — the managed tier provides the LLM. You run agent teams from a web UI without installing Ollama or managing API keys.

From Python:

```python
from antcrew import DevTeam
from antcrew.models import OllamaModel, AnthropicModel, SimulatedLLM

# Local — no API key
team = DevTeam(model=OllamaModel("llama3"))

# Cloud
team = DevTeam(model=AnthropicModel("claude-sonnet-4-6"))

result = team.run("Build a REST API for user authentication")
print(result.state["prd"].title)           # PRD object
print(len(result.state["tickets"]))        # list[Ticket]
print(result.cost_usd)                     # e.g. 0.43 (0.0 with Ollama)
```

**Inspect the trace after any run:**

```bash
antcrew inspect <run-id>
# Shows: prompt, response, tokens, cost, governance hash — per agent
antcrew trace replay <run-id>
# Replays every agent call to detect model drift
```

---

## Teams

| Team | Agents | Best for |
|---|---|---|
| `DevTeam` | BA → PM → BackendDev | Backend features, APIs |
| `FullStackTeam` | BA → PM → Backend → Frontend → QA → Reviewer → DevOps → DocWriter | Full-stack MVPs |
| `ResearchTeam` | Researcher → Writer | Technical research, blog posts |
| `ContentTeam` | Idea → Copywriter → Editor | Marketing content, docs |
| `CustomTeam` | User-defined steps (code or YAML) | Fully custom pipelines |
| `Router` | Classifier → dispatches to any team | Smart routing |
| `LegalReviewTeam` | ClauseExtractor → RiskFlagging → LegalReviewer | Contract review with risk scoring |
| `CodeMigrationTeam` | Scanner → Planner → Migrator → Verifier | Automated codebase migration |
| `ReproducibleResearchPipeline` | ResearchTeam + full-trace + governance_hash | Reproducible AI research |
| `BrandVoiceContentTeam` | ContentTeam + ChromaMemory per-brand | On-brand content at scale |
| `WhiteLabelWrapper` | Wraps any team with markup billing | Agency / reseller billing |

```python
from antcrew import (
    DevTeam, FullStackTeam, ResearchTeam, ContentTeam, CustomTeam, Router,
    LegalReviewTeam, CodeMigrationTeam, ReproducibleResearchPipeline,
    BrandVoiceContentTeam, BrandVoiceProfile, WhiteLabelWrapper,
)
```

---

## Features

- **LLM-agnostic.** Anthropic, OpenAI, Gemini, Groq, Azure, Ollama, LM Studio, LiteLLM (100+ providers). Mix models per agent.
- **Local-first.** Run entirely on your machine with Ollama — no API keys, no data leaves your network.
- **Typed artifacts.** PRDs, tickets, code, tests, and docs are Pydantic objects — predictable, auditable, and serializable.
- **TraceLog.** Every agent call is written to a local SQLite database. `antcrew inspect <id>` and `antcrew trace replay <id>` work offline.
- **Governance hash.** Each agent configuration produces a deterministic SHA-256 hash — cite in papers, pin in CI.
- **Human-in-the-loop.** `FlexibleHITL` pauses any checkpoint for local approval (callback) or remote review (via antcrew-platform).
- **Semantic memory.** ChromaDB or in-memory vector store. Agents reference decisions from past runs.
- **EvalSuite.** Regression testing for agent outputs. Run in CI with `antcrew eval`.
- **MCP tools.** Any MCP-compatible tool server works out of the box.
- **Project sessions.** Tickets, code, and docs accumulate across multiple runs instead of starting fresh.
- **Real sandbox execution.** Generated tests run in a subprocess or Docker container and results feed back into state.
- **Retry + resilience.** Exponential-backoff retry on timeouts, rate limits, and transient errors.

---

## Specialized teams

**Legal review:**

```python
from antcrew import LegalReviewTeam

team = LegalReviewTeam()
result = team.run(nda_text)
finding = result.state["legal_finding"]
print(f"High-risk clauses: {finding.high_risk_count}, approved: {finding.approved}")
```

**Reproducible research — cite and replay:**

```python
from antcrew import ReproducibleResearchPipeline

pipeline = ReproducibleResearchPipeline(db_path="experiments.db")
exp = pipeline.run("What are the failure modes of multi-agent AI systems?")
print(exp.experiment_id)   # "<team_hash>:<run_id>" — stable identifier

# Replay later to detect model drift
for call in pipeline.replay_experiment(exp.experiment_id):
    print(call["agent_name"], "matched:", call["matched"])
```

**Brand voice content:**

```python
from antcrew import BrandVoiceContentTeam, BrandVoiceProfile

profile = BrandVoiceProfile(
    name="Acme Corp",
    tone="Professional but approachable",
    standards=["Always end with a CTA", "Use 'you' not 'users'"],
    examples=["Our API ships same-day — because waiting is so 2019."],
)
team = BrandVoiceContentTeam(brand=profile)   # requires pip install antcrew[memory]
result = team.run("Write a product launch announcement")
```

**White-label billing:**

```python
from antcrew import DevTeam, WhiteLabelWrapper

billing = WhiteLabelWrapper(DevTeam(), client_label="acme-corp", markup_pct=200)
record = billing.run("Build a REST API for a todo app")
print(f"Billed: ${record.billed_usd:.4f}  Margin: {record.margin_pct:.1f}%")
```

---

## CLI reference

```bash
antcrew run "goal"              # run a team locally
antcrew run "goal" --model ollama:llama3   # offline, no API key
antcrew init                    # scaffold a new project interactively
antcrew inspect <run-id>        # view trace: prompts, tokens, cost, governance hash
antcrew trace replay <run-id>   # replay all agent calls
antcrew eval                    # run EvalSuite regression tests
antcrew describe                # show pipeline data flow (consumes/produces)
antcrew serve                   # local web dashboard
antcrew cost                    # usage and cost summary
antcrew dag                     # visualize agent graph
```

Run `antcrew --help` for the full list of 29 commands.

---

## Optional extras

```bash
pip install "antcrew[memory]"    # ChromaDB semantic memory
pip install "antcrew[litellm]"   # 100+ LLM providers via LiteLLM
pip install "antcrew[slack]"     # Slack HITL + notifications
pip install "antcrew[telegram]"  # Telegram notifications
pip install "antcrew[mcp]"       # MCP tool servers
```

---

## Architecture

antcrew is two packages shipped together:

| Layer | Package | What it does |
|---|---|---|
| Layer 1 | `antcrew` | Named-role teams (BA, PM, Dev…) orchestrated with LangGraph. HITL, sessions, memory. |
| Layer 2 | `antcrew-engine` | Goal-directed `EngineLoop` — capabilities selected at runtime until conditions are satisfied. |

`pip install antcrew` installs both. You don't need to install or import `antcrew-engine` directly — all its capabilities (`Architect`, `CodeGenerator`, `TestRunner`…) are re-exported from `antcrew`. The separation exists so `antcrew-engine` can be used standalone without LangGraph — useful if you're building a custom execution layer on top.

---

## When to use antcrew-platform (optional)

The SDK runs entirely locally — no cloud account required. **antcrew-platform** is the optional SaaS layer for teams that need:

- **Multi-workspace** concurrent runs with cost roll-up
- **Remote HITL** — reviewers approve from Slack or a web link, not a terminal
- **Dashboard** — live run stream, eval trends, cost charts
- **GitHub App** — auto-post explainability comments on PRs
- **Webhook delivery** — push run events to your systems

[→ antcrew-platform](https://github.com/iagop03/antcrew-platform)

---

## License

MIT
