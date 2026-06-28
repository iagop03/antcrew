# Contributing to AntCrew

## Quick start

```bash
git clone https://github.com/iagop03/antcrew.git
cd antcrew
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest    # ~1500 tests, no API keys needed
```

## How the code is organized

```
antcrew/
  agents/          Individual agent classes (BackendDevAgent, PMAgent, …)
  agents/registry.py  Central registry — add new agents here
  cli/             CLI commands (one module per command group)
  core/            Supervisor, Pipeline, BaseLLM, state graph
  models/          LLM adapters (Anthropic, OpenAI, Ollama, …)
  teams/           Pre-built team compositions (DevTeam, FullStackTeam, …)
  integrations/    Jira, GitHub, Confluence, Slack, Telegram
  memory/          Vector store backends (ChromaDB, in-memory)
  eval/            Evaluation framework and metrics
  trace.py         TraceLog — SQLite-backed run/cost observability
```

## Making a change

1. Create a branch: `git checkout -b feat/your-feature`
2. Write tests first — every new feature needs at least one test using `SimulatedLLM`
3. Run `pytest` (no API keys needed) and `python -m ruff check .`
4. Open a PR against `main`

## Adding a new agent

1. Create `antcrew/agents/my_agent.py` inheriting from `BaseAgent`
2. Declare `consumes`, `produces`, and `role_description` class attributes
3. Register it in `antcrew/agents/registry.py`:
   ```python
   "my_agent": ("antcrew.agents.my_agent", "MyAgent"),
   ```
4. Add a test in `tests/` using `SimulatedLLM`

## Adding a CLI command

All CLI commands live in `antcrew/cli/`. Each module registers its commands on the
shared `app` (or `_flow_app` / `_project_app`) Typer instance from `_app.py`.

## Good first issues

- Add cost-per-agent breakdown to `antcrew history` output
- Add `--since` filter to `antcrew benchmark` (mirrors `antcrew history --since`)
- Add `antcrew agents --json` flag for machine-readable output
- Add a `RepoIndex` example to the README's Semantic Memory section
- Support `antcrew describe --trace` for any trace path without `~/.antcrew/trace.db`

## Running linting

```bash
python -m ruff check .
python -m ruff check . --fix   # auto-fix safe errors
```

## Tests

```bash
pytest                          # full suite
pytest tests/test_cli_run.py    # single file
pytest -k "test_dev_team"       # by keyword
pytest --tb=short -q            # concise output
```

All tests use `SimulatedLLM` — no API keys, no network calls, no cost.
