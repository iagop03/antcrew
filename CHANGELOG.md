# Changelog

All notable changes to AntCrew are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.8.2] — 2026-06-27

### Added
- **`TemplateAgent`** — define custom agents in YAML/JSON without writing Python.
  - Declare `name`, `system_prompt`, optional `input_key` / `output_key`,
    `role_description`, and `max_tokens` in a plain config file or dict.
  - `load_template_agent(path, llm)` convenience factory for loading from a file.
  - Inline template agents in `agentteam.yaml`: any agent block that includes
    `system_prompt:` (or a `template:` file reference) is automatically
    constructed as a `TemplateAgent` — no custom Python class required.
  - Exported from the top-level `antcrew` namespace.
  - 32 tests in `tests/test_template_agent.py`.
- **`TraceLog`** — SQLite-backed per-run recorder for agent call data.
  - Records timing, token counts, and cost for every `llm.system()` call.
  - Attach to any team with `team._trace_log = TraceLog(path)` or via
    `antcrew run --trace <file.db>`.
  - Read API: `list_runs()`, `get_run()`, `get_calls()`, `list_runs_filtered()`,
    `get_stats()` — use directly or through the CLI commands below.
- **`antcrew history <trace.db>`** — aggregate statistics browser for TraceLog files.
  - Summary panel: total runs, success rate, cost, token usage, date range.
  - By-team breakdown table (hidden when `--team` filter is active).
  - Filterable run table with `--team`, `--status` (done/error/all),
    `--since` (YYYY-MM-DD or relative, e.g. `7d`), and `--limit`.
  - `--stats` prints the summary panel only (no per-run table).
  - `--export <file.csv>` exports the filtered run list to CSV.
  - 24 tests in `tests/test_cli_history.py`.
- **`antcrew.core.validation`** — internal Pydantic helper (`_validate_schema`)
  supporting both `BaseModel` subclasses and generic type annotations via
  `TypeAdapter`.
- **`antcrew.testing`** — public testing sub-package; `SequencedLLM` is now
  importable from `antcrew.testing` in addition to `antcrew.testing.llms`.

---

## [0.8.1] — 2026-06-26

### Added
- **`antcrew test <state.json>`** — new CLI command that runs the QA-generated
  test artifacts from any saved pipeline state file without re-running the
  full pipeline.
  - Supports `--runner local` (default, temp-dir subprocess) and
    `--runner docker` (isolated container via `DockerRunner`).
  - `--keep <dir>` writes test + code files to the specified directory and
    keeps them after the run — useful for debugging failing tests.
  - `--no-code` runs test files only, omitting code artifacts (useful when the
    implementation is already checked out locally).
  - `--verbose` / `-v` always prints the full pytest output; without it,
    output is shown only on failure.
  - `--timeout` controls the per-run pytest timeout (default 60 s).
  - Handles nested project state files (`{"state": {...}}`) automatically.
  - Exit code 0 when all tests pass, 1 on failure — suitable for CI scripts.
  - 22 new tests in `tests/test_cli_test_cmd.py`.

---

## [0.8.0] — 2026-06-26

### Added
- **Native JSON mode / structured outputs** — `BaseLLM.complete()` gains a
  `json_mode: bool = False` keyword argument.  Adapters that support native
  JSON mode use it; others accept and silently ignore the flag.
  - **`OpenAIModel`** — passes `response_format={"type": "json_object"}` on
    the blocking path (`_complete_blocking`).  Streaming and reasoning models
    (`o1`/`o3`) are not affected.
  - **`GeminiModel`** — sets `generationConfig.responseMimeType =
    "application/json"` for both streaming and non-streaming paths.
  - **`AzureOpenAIModel`** — inherits OpenAI behaviour automatically.
  - **`AnthropicModel`**, **`GroqModel`**, **`OllamaModel`**,
    **`SimulatedLLM`** — accept `json_mode` as a no-op; callers fall back to
    the existing retry-with-hint logic in `system_parsed`.
  - **`FallbackLLM`** — propagates `json_mode` to every model in the chain.
- **`BaseAgent.system_parsed()` auto-activates JSON mode** — every call to
  `system_parsed()` (including retries) now passes `json_mode=True` to the
  underlying LLM so that providers that support it return well-formed JSON
  immediately, reducing parse errors and retry round-trips.
- **`antcrew.testing.llms.SequencedLLM`** — signature updated to accept
  `json_mode` for compatibility with `system_parsed` test scenarios.

### Changed
- `BaseLLM.complete()` abstract signature now includes `json_mode: bool =
  False`.  All concrete adapters and test helpers updated accordingly.

---

## [0.7.0] — 2026-06-26

### Added
- **`AzureOpenAIModel`** — Azure OpenAI endpoint adapter.  Inherits all
  streaming, retry, cost-tracking, and reasoning-model logic from
  `OpenAIModel`; the only difference is the `AzureOpenAI` SDK client.
  Reads `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
  `AZURE_OPENAI_API_VERSION` from env.  Deployment names starting with
  `o1`/`o3` are automatically routed to the reasoning path.
  `build_llm("azure:<deployment>")` added.  Exported from `antcrew`
  top-level.
- **`antcrew lint`** — static validation of `agentteam.yaml` / flow files
  with no LLM calls or API keys required.  Checks: unknown team/model/
  channel/runner, flow cycles (DFS), invalid `max_cost_usd`, unresolved
  `${VAR}` tokens, unknown agent names, unknown preset names.  Severity
  levels: `error` (exit 1), `warning` (advisory), `info` (defaults).
  `--strict` promotes warnings to errors; `--quiet` hides info messages.
  Defaults to `agentteam.yaml` in cwd.
- **Async teams** — `AsyncDevTeam`, `AsyncFullStackTeam`,
  `AsyncResearchTeam`, `AsyncContentTeam`.  Each is a thin subclass of the
  synchronous counterpart with an `async def run()` that delegates to
  `asyncio.to_thread` so blocking LLM calls never stall the event loop.
  `run_sync()` alias preserves direct synchronous access.  Compatible with
  FastAPI route handlers, Jupyter `await`, and `asyncio.gather`.  All four
  exported from `antcrew` top-level.
- **Automatic memory injection** — `BaseAgent.system()` now searches
  `self.memory` before every LLM call and prepends a
  `[Relevant context from memory]` block to the user message when relevant
  entries are found.  Behaviour is controlled by two new constructor params:
  `memory_n=3` (max results) and `memory_score_threshold=0.0` (min Jaccard
  score).  Activates only when `memory=` is attached to the agent (no
  change for agents without memory).  Falls back silently on errors.

---

## [0.6.0] — 2026-06-26

### Added
- **Rate-limit auto-retry improvements** — `BaseLLM._retry_delay_for()` now
  honours the `Retry-After` response header when the provider sends it
  (both `Retry-After` and `retry-after` spellings).  Backoff is capped at
  `max_retry_delay` (default 60 s) and `retry_jitter` (default 0.5 s) of
  uniform noise is added to prevent thundering-herd bursts on 429 responses.
  Each retry emits a structured `log.warning()` with attempt number, delay,
  agent name, and exception type.  Streaming path now also uses
  `_with_retry`; `on_token` is cleared for fallback attempts so the progress
  panel does not receive duplicate partial output.  New `BaseLLM` class
  attrs: `max_retry_delay`, `retry_jitter`.
- **`antcrew graph`** — new CLI command that renders the Supervisor agent
  flow as ASCII art or a Mermaid diagram, with no LLM calls required.
  `antcrew graph --team dev`, `antcrew graph --config agentteam.yaml`,
  `antcrew graph --format mermaid`.  Linear chains render as a single
  `[START] → A → B → [END]` line; branching/conditional graphs use a
  topologically-sorted edge list.  Mermaid conditional edges appear as
  labelled arrows (`-->|condition|`).  Auto-loads `agentteam.yaml` from the
  current directory when no `--team` or `--config` is given.
  New `antcrew/graph.py` module: `render_ascii()`, `render_mermaid()`,
  `_get_builtin_flow()`.
- **Agent presets** — named prompt-style modifiers applied to every system
  call.  Four built-ins: `CONCISE`, `STRICT`, `VERBOSE`, `CAREFUL`.
  Any `BaseAgent` now accepts `preset=` as a string name or `AgentPreset`
  instance.  The preset instruction is prepended before the base system
  prompt and before `system_prompt_suffix`.  YAML `agentteam.yaml` supports
  a `preset:` key per agent.  All presets exported from `antcrew` top-level.
  Custom presets: `AgentPreset("name", "Your instruction here.")`.
- **OpenAI model enhancements** — `OpenAIModel` fully supports the `o1` /
  `o3` reasoning model family (`o1`, `o1-mini`, `o3`, `o3-mini`): uses
  `max_completion_tokens` instead of `max_tokens`, skips streaming (which
  the API does not support for these models), and cost-estimates correctly
  via the updated `_COST_TABLE`.  New `build_llm()` shortcuts: `o1`,
  `o3-mini`, and `openai:<model>` explicit prefix.  `OpenAIModel` exported
  from `antcrew` top-level (is `None` when the `openai` package is not
  installed).  Cost table extended with `gpt-4-turbo`, `gpt-3.5`, and all
  `o1`/`o3` variants.

---

## [0.5.0] — 2026-06-26

### Added
- **Agent tools** (`BaseTool`, `WebSearchTool`, `CodeExecutorTool`,
  `ReadFileTool`) — callable capabilities injected into agents.  Any
  `BaseAgent` accepts `tools=[...]` and `max_tool_steps=N`.  Calling
  `agent.system_with_tools(system, user)` runs a ReAct loop: tool schemas
  are injected into the system prompt; `<tool_call>` XML blocks in the
  response are parsed and dispatched; results are fed back as conversation
  history until the model gives a plain-text final answer or step limit is
  reached.  All types exported from `antcrew` top-level.
  - `WebSearchTool` — DuckDuckGo Instant Answer API (no key required).
  - `CodeExecutorTool` — isolated Python subprocess with 15 s timeout.
  - `ReadFileTool` — reads local files up to a configurable size cap.
- **`Pipeline`** — chains multiple teams sequentially.
  `Pipeline([ResearchTeam(llm), DevTeam(llm)]).run(request)` carries
  artifact keys (`prd`, `tickets`, `code_artifacts`, `research_document`,
  etc.) from each team's output into the next team's initial state.
  `RunResult.cost_usd` aggregates cost across all teams.  The set of
  forwarded keys is configurable via `carry_keys`.  Exported from
  `antcrew` top-level.
- **`antcrew watch <path>`** — file-watcher dev loop.  Watches a file or
  directory; re-runs the pipeline on every save and shows a unified
  artifact diff between runs.  `--debounce N` (default 2 s),
  `--no-diff`.  Requires `pip install antcrew[watch]` (`watchdog>=4.0`).
  New `watch` optional dependency added to `pyproject.toml`.
- **`antcrew benchmark <cases.json>`** — batch pipeline evaluation.
  Runs a list of `{request, team, label}` cases, collects elapsed time,
  cost, and artifact counts per case, and prints a Rich table.  Flags:
  `--parallel N`, `--output results.json`, `--model`, `--timeout`.
  Empty requests are skipped; unknown teams are recorded as errors without
  crashing the batch.

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
