# Changelog

All notable changes to AntCrew are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.2.1] — 2026-06-25

### Added
- `SqliteSaver` — LangGraph SQLite checkpointer exposed as `antcrew.checkpointers.SqliteSaver` and importable from the top-level `antcrew` namespace. Threads with the same `thread_id` survive process restarts.
- `checkpointer=` constructor param on all four teams (`DevTeam`, `FullStackTeam`, `ResearchTeam`, `ContentTeam`). Pass any `BaseCheckpointSaver` — `SqliteSaver`, `MemorySaver`, or a custom backend.
- `antcrew run --checkpointer <file.db>` CLI flag. Combines with `--thread` to resume a prior run from disk.
- `[sqlite]` extra now includes `langgraph-checkpoint-sqlite>=2.0` (was only `aiosqlite>=0.19`).
- `max_cost_usd` — per-run cost budget (USD) on all four teams. The pipeline raises `CostLimitExceeded` before the next LLM call when the limit is reached. Budget resets at the start of each `run()` call. Example: `DevTeam(max_cost_usd=2.0)`.
- `CostLimitExceeded` exception — importable from `antcrew` top-level. Attributes: `cost_usd` (amount spent), `limit_usd` (the configured limit).
- `antcrew run --max-cost <USD>` CLI flag. Example: `antcrew run "..." --max-cost 1.50`.
- `max_cost_usd:` key in `agentteam.yaml` YAML/JSON config.

### Requires
- `pip install antcrew[sqlite]` for `SqliteSaver` support.

---

## [0.2.0] — 2026-06-25

### Added
- `RunResult` — typed return value of `team.run()`. Exposes `state`, `thread_id`, `cost_usd`. Backward-compatible: `result["prd"]`, `result.get("tickets")`, `"key" in result` all work unchanged. `SandboxRunResult` alias preserves the old sandbox `RunResult` at the top-level import.
- `consumes` / `produces` class-level attributes on all 15 built-in agents — explicit data contracts readable without opening source code.
- `antcrew describe` CLI command — prints a Rich table of pipeline agents, their `consumes`/`produces` fields, and a coherence check. Works without API keys or LLM instantiation.
- `system_parsed()` on `BaseAgent` — calls the LLM, extracts JSON via `_extract_json` + `_json_loads`, validates against an optional Pydantic schema, and raises a structured `ParseError` on failure (log-friendly, no silent corruption).
- `SprintPlannerAgent` added to full agent migration with `consumes`/`produces` metadata.
- `save_state()` in `antcrew.utils.persistence` now transparently accepts `RunResult` in addition to plain dicts.
- `cli` extra (alias — typer + rich are core deps) and `sqlite` extra (`aiosqlite>=0.19`, for SqliteSaver in v0.2.1).

### Changed
- All four teams (`DevTeam`, `FullStackTeam`, `ResearchTeam`, `ContentTeam`) return `RunResult` from `run()` instead of a raw `TeamState` dict. Dict access patterns are unchanged.
- `Project.run()` still returns a plain `dict` (unwraps `RunResult.state`) — no change to existing project consumers.
- `antcrew.__version__` set to `"0.2.0"`.
- Package development status updated to `3 - Alpha`.

### Breaking changes (within 0.x semver)
- `from antcrew import RunResult` now imports the team-level `RunResult` (not the sandbox one). Use `from antcrew import SandboxRunResult` to access the old sandbox result type.
- `isinstance(team.run(...), dict)` now returns `False` — use `isinstance(result, RunResult)` or rely on the dict-like interface.

---

## [0.1.0] — 2026-06-18

Initial release — in production at Font Jardineria.

### Core
- `DevTeam`, `FullStackTeam`, `ResearchTeam`, `ContentTeam` — multi-agent pipelines built on LangGraph
- `Supervisor` — configurable pipeline graph with custom flow order
- `InteractiveMixin` — HITL loop with approve / reject / edit / conversational feedback
- Typed artifacts: `PRD`, `Ticket`, `CodeArtifact`, `TestArtifact`, `CodeReview`, `DevOpsArtifact`, `DocumentationArtifact`, `ResearchDocument`, `ContentPiece`

### Models
- `AnthropicModel` (Claude), `OpenAIModel` (GPT / DeepSeek / Mistral), `GeminiModel`, `GroqModel`, `OllamaModel`, `SimulatedLLM`
- `FallbackLLM` — automatic fallback chain across multiple providers
- `LLMCache` — in-memory prompt/response cache
- `FileLLMCache` — SQLite-backed persistent cache; survives restarts
- Token tracking and estimated cost per agent and per run
- Exponential-backoff retry on rate limits and transient errors
- Token streaming via `on_token` callback

### Project sessions
- `Project` — accumulates tickets, code files, and docs across multiple `run()` calls
- Context enrichment: subsequent runs receive a summary of prior PRDs, tickets, and code so agents build on previous work
- Auto-save to JSON after every run; `Project.load()` restores team from stored spec
- `load_context()` / `TeamContext` — load team + project from a single YAML/JSON config file

### CLI (`antcrew`)
- `antcrew run` — autonomous pipeline run with optional `--project` and `--cache` flags
- `antcrew interactive` — HITL loop with per-agent approve / reject / edit / feedback prompts
- `antcrew project run / show / history` — manage persistent project sessions
- `antcrew eval` — run evaluation cases and score results; supports LLM judge
- `antcrew serve` — FastAPI REST server + SSE streaming endpoint
- `antcrew flow show / validate` — inspect and validate YAML/JSON flow definitions
- `antcrew init` — generate starter `agentteam.yaml` + `main.py`
- `antcrew show` — display a saved state JSON file

### YAML / JSON config
- `team:`, `model:`, `agents:`, `flow:`, `channel:`, `channels:` — full team config
- `cache:` — attach `FileLLMCache` automatically
- `project:` — attach persistent `Project` automatically
- `runner:` — configure `LocalRunner` or `DockerRunner` for test execution
- `${VAR}` env-var expansion; supports `.yaml` and `.json` formats

### Sandbox
- `LocalRunner` — executes generated tests in a temp directory on the host
- `DockerRunner` — executes tests in a fresh Docker container (real isolation, no host writes, configurable memory/network/CPU limits)

### Flows
- `load_flow()`, `validate_flow()`, `format_flow()` — define pipeline graphs in YAML/JSON without writing Python

### Integrations
- `JiraIntegration` — sync tickets to Jira
- `GitHubIntegration` — create branches, upsert files, open PRs
- `ConfluenceIntegration` — publish PRDs and docs to Confluence
- `SlackChannel` — HITL reviews via Slack
- `TelegramChannel` — HITL reviews via Telegram (optional dep: `pip install antcrew[telegram]`)
- `ConsoleChannel` — terminal HITL with Rich; free-text feedback for conversational agents

### Memory
- `InMemoryMemory` — word-overlap search, no dependencies
- `ChromaMemory` — embedding-based persistent memory via ChromaDB (`pip install antcrew[memory]`)

### REST API
- `POST /run`, `GET /run/{id}`, `GET /run/{id}/stream` (SSE), `GET /run/{id}/artifacts`
- `POST /eval`, `GET /eval/{id}`, `GET /evals`
- Persistence across restarts via `ANTCREW_DATA_DIR`

### Package
- `py.typed` marker — full mypy / pyright support
- `build_llm()`, `build_runner()` — public config utilities
- `python-telegram-bot` moved to optional `[telegram]` extra
- MIT license
