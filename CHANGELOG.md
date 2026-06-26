# Changelog

All notable changes to AntCrew are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.4.0] — 2026-06-26

### Added
- **Per-agent LLM** (`agent_models=` param on all four teams) — assign a
  different `BaseLLM` instance to any individual agent. Unspecified agents
  fall back to the team `model`. Cost aggregation in `RunResult.cost_usd`
  and trace propagation both work across all unique LLMs automatically.
- **Agent parallelism** (`ParallelGroup` + `parallel()` helper in
  `antcrew.core.supervisor`) — wrap any set of agents into a concurrent
  node that runs them simultaneously via `ThreadPoolExecutor`. State
  updates are merged automatically: list fields are concatenated, dict
  fields are union-merged, scalars use last-write. Cuts wall-clock time
  by ~40 % for independent groups (e.g. `backend_dev` + `frontend_dev`).
  `_unique_llms()` recurses into groups for correct cost/trace handling.
- **`antcrew diff <run-a.json> <run-b.json>`** — compare two saved
  pipeline runs. Shows differences in request, PRD (title, summary),
  tickets (added / removed by id), and code artifacts (added / removed /
  modified with inline unified diff per changed file). `--no-files`
  suppresses content diffs; `--context N` controls unified diff lines.
- **`antcrew export <run.json>`** — bundle all generated artifacts from a
  saved run into a deflate-compressed zip archive. Layout:
  `src/` (code), `tests/`, `devops/`, `docs/`, `state.json`. Flags:
  `--output`, `--no-tests`, `--no-devops`, `--no-docs`, `--no-state`.
  Research documents are exported as `docs/<title>.md`.

---

## [0.3.0] — 2026-06-26

### Added
- `retry-with-hint` in `BaseAgent.system_parsed()` — when `max_retries > 0`,
  failed parse or schema-validation attempts are retried automatically. Each
  retry appends the previous error and up to 500 chars of the invalid response
  to the user message so the model can self-correct. `parse_failure` warning is
  now logged only after all attempts are exhausted; intermediate failures are
  logged at DEBUG level.
- `FallbackLLM` cost guard — `FallbackLLM.system()` previously bypassed the
  `max_cost_usd` guard because it overrides `BaseLLM.system()` directly. Fixed
  by adding an aggregate cost check at the top of `FallbackLLM.system()` using
  `get_usage_summary()` (which sums across all inner models). Budget now applies
  correctly to the total spend regardless of which model in the chain is called.
- `FallbackLLM` trace propagation — `trace` and `_trace_run_id` are now
  included in `FallbackLLM.__setattr__` propagation so `BaseLLM.system()` trace
  hooks fire on every inner model. Trace records were previously silently dropped
  when a `FallbackLLM` was used as the team LLM.
- `antcrew publish <state.json>` CLI command — publishes pipeline output to
  external systems from a saved state file:
  - `--github` — creates a branch, commits `code_artifacts` + `devops_artifacts`,
    and opens a GitHub PR. Credentials via `--token` / `$GITHUB_TOKEN`.
  - `--confluence` — publishes PRD, research document, and doc artifacts as
    Confluence pages. Credentials via env vars or flags.
  - Both flags can be combined in a single invocation.
  - State dict is re-hydrated into Pydantic objects (`PRD`, `CodeArtifact`,
    `DevOpsArtifact`, `DocumentationArtifact`, `ResearchDocument`) before
    integrations receive it so attribute access works correctly.
- Dashboard SPA — `antcrew serve` now ships a fully functional web UI at `/ui/`
  (single-file vanilla HTML/JS, no build step, no npm dependencies):
  - **Runs tab**: auto-refreshing table with status badges; 2 s poll while any
    run is live, 10 s otherwise.
  - **New run modal**: trigger a run with request, team, model, and optional
    thread ID.
  - **Detail panel**: live-streaming tokens via `EventSource` with per-agent
    labels, then PRD, ticket list, file list with inline code viewer, and usage
    stats (input/output tokens + estimated cost).
  - **Evals tab**: score bars, per-agent scores, pass/fail indicator.
  - `GET /` now redirects to `/ui/`.
  - `antcrew serve` prints the current `__version__` instead of a hardcoded
    string.

### Fixed
- `FallbackLLM.system()` silently bypassed `max_cost_usd` — now raises
  `CostLimitExceeded` using the correct aggregate cost.
- `FallbackLLM` with `trace_log=` attached to the team no longer drops trace
  records for inner-model calls.
- `antcrew serve` printed `v0.4` regardless of the installed version.

---

## [0.2.1] — 2026-06-26

### Added
- `SqliteSaver` — LangGraph SQLite checkpointer exposed as `antcrew.checkpointers.SqliteSaver` and importable from the top-level `antcrew` namespace. Threads with the same `thread_id` survive process restarts.
- `checkpointer=` constructor param on all four teams (`DevTeam`, `FullStackTeam`, `ResearchTeam`, `ContentTeam`). Pass any `BaseCheckpointSaver` — `SqliteSaver`, `MemorySaver`, or a custom backend.
- `antcrew run --checkpointer <file.db>` CLI flag. Combines with `--thread` to resume a prior run from disk.
- `[sqlite]` extra now includes `langgraph-checkpoint-sqlite>=2.0` (was only `aiosqlite>=0.19`).
- `max_cost_usd` — per-run cost budget (USD) on all four teams. The pipeline raises `CostLimitExceeded` before the next LLM call when the limit is reached. Budget resets at the start of each `run()` call. Example: `DevTeam(max_cost_usd=2.0)`.
- `CostLimitExceeded` exception — importable from `antcrew` top-level. Attributes: `cost_usd` (amount spent), `limit_usd` (the configured limit).
- `antcrew run --max-cost <USD>` CLI flag. Example: `antcrew run "..." --max-cost 1.50`.
- `max_cost_usd:` key in `agentteam.yaml` YAML/JSON config.
- `TraceLog` — SQLite-backed per-agent call recorder. Records one row per `llm.system()` invocation with agent name, timing (ms), token counts, and estimated cost. Importable from `antcrew` top-level.
- `trace_log=` constructor param on all four teams. Wraps each `run()` with `begin_run`/`end_run` book-keeping; populates `agent_calls` via the `BaseLLM.system()` hook.
- `antcrew run --trace <file.db>` CLI flag. Activates TraceLog for a single run.
- `antcrew trace <file.db>` CLI command — lists recent runs or shows per-agent call detail for a specific run (`--run <id>` or `--thread <id>`).
- `BaseLLM.system()` refactored to use a single `result` variable (no multiple early-returns); timing is injected via `time.monotonic()` only when `trace` is attached.
- `antcrew replay <thread_id> --checkpointer <db>` CLI command — resumes a pipeline from its last SqliteSaver checkpoint. With `--trace <db>`, auto-detects the original request and team from TraceLog so no re-specification needed.

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
