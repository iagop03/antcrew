# AntCrew

Multi-agent framework for software dev teams, built on LangGraph. LLM-agnostic — cloud or local.

> **Status:** early development (v0.1). Core architecture is being extracted and generalized. Not yet on PyPI.

## What is this?

AntCrew lets you spin up an autonomous software development team made of specialized AI agents — Business Analyst, PM, Backend Developer, and more — that collaborate through a shared state graph (powered by [LangGraph](https://github.com/langchain-ai/langgraph)) to take a project from requirements to working code.

It's designed for a specific sweet spot: **new MVPs, small-to-medium projects (0–~50k lines), or new modules inside an existing system.** It is not aimed at maintaining large legacy codebases or replacing large human teams.

Key ideas:

- **LLM-agnostic.** Run it with Claude, GPT, Groq, local models via Ollama, or LM Studio. Mix models per agent — use a cheaper/local model for routine coding tasks and a stronger one for reasoning-heavy roles like PM or Reviewer.
- **Local-first option.** Run entirely on your own machine with no API keys and no code leaving your environment.
- **Human-in-the-loop by design.** Agents propose, humans approve. Nothing ships to production without explicit sign-off.
- **Typed artifacts.** PRDs, tickets, code changes, and PRs are structured objects, not free-form text, so the team's output is predictable and auditable.

## Quick example

```python
from antcrew import DevTeam
from antcrew.models import OllamaModel, AnthropicModel
from antcrew.integrations import JiraIntegration, GitHubIntegration

# 100% local — no API keys, no code leaves your machine
team = DevTeam(
    model=OllamaModel("llama3", base_url="http://localhost:11434"),
    integrations=[
        JiraIntegration(project="MYAPP"),
        GitHubIntegration(repo="myorg/myapp"),
    ],
)

# Or with Claude in the cloud
team = DevTeam(
    model=AnthropicModel("claude-sonnet-4-6"),
)

team.run("Add a password reset flow to the auth module")
```

> The API above reflects the current design target — some pieces are still being built. See the roadmap below for what's working today.

## Architecture (high level)

```
┌─────────────────────────────────────────┐
│              SharedMemory                │  ← central state (LangGraph)
└───────────────┬───────────────────────-──┘
                 │
   ┌─────────────┼──────────────┐
   │             │              │
┌──▼───┐     ┌───▼──┐      ┌────▼─────┐
│  PM  │     │ Biz  │      │ Backend  │   ← agents (pluggable)
│Agent │     │Agent │      │   Dev    │
└──┬───┘     └───┬──┘      └────┬─────┘
   │              │              │
   └──────────────┴──────────────┘
                  │
        ┌─────────▼──────────┐
        │    Integrations    │   ← Jira, GitHub, Slack, Telegram
        └─────────────────────┘
```

Every agent reads from and writes to `SharedMemory`, produces typed artifacts, and can be backed by any supported model.

## Roadmap

| Version | Focus |
|---|---|
| **v0.1** *(current)* | Extract core from TrAIn, generalize agents, `BaseLLM` + Anthropic/Ollama/Groq models |
| v0.2 | CLI, PyPI release, Jira/GitHub/Slack integrations, basic Human-in-the-Loop |
| v0.3 | Advanced HITL (per-agent channels), simulation mode, docs site, demos |
| v0.4 | Web dashboard, audit log, session replay, semantic memory, conflict detection agent |
| v1.0 | Plugin system, security/cost/retrospective agents, premium templates, hosted SaaS |

## Installation

Not yet published. Once v0.2 lands:

```bash
pip install antcrew
```

For now, clone the repo and install in editable mode:

```bash
git clone https://github.com/<your-org>/antcrew.git
cd antcrew
pip install -e .
```

## Contributing

This project is in early, active development — the architecture is still settling, so it's a great time to influence direction. Issues and discussions are welcome. A `CONTRIBUTING.md` with guidelines will land alongside the v0.2 CLI release.

## License

[MIT](LICENSE)
