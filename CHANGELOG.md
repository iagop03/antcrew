# Changelog

All notable changes to AntCrew are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.33.4] — 2026-07-09

### Fixed
- `__version__` dynamic read via `importlib.metadata` now actually shipped in the published wheel — 0.33.1 on PyPI still contained the hardcoded string; 0.33.2 is the first release where `antcrew.__version__` matches the installed version at runtime.

### Added
- README clarification: `antcrew engine` vs `antcrew-engine run` — both invoke the same engine logic; the distinction is whether LangGraph (Layer 1) is present.

---

## [0.33.1] — 2026-07-09

### Fixed
- `__version__` now reads dynamically from installed package metadata (`importlib.metadata`) instead of a hardcoded string — `antcrew doctor` will always show the correct installed version.
- Shim backward-compat: `antcrew.engine` now exports both the new names (`EngineLoop`, `EngineDecision`, `EngineLoopError`) and the old names (`Operator`, `OperatorDecision`, `OperatorError`) — code written against pre-0.33.0 continues to work.
- Private helpers (`_extract_failing_files`, `_next_pending`, `_safe_parse_review`, `_safe_parse_tasks`, `_to_test_path`) now explicitly re-exported from capability shims — wildcard `import *` was silently skipping them.
- `antcrew/engine/operator.py` submodule shim now exports `EngineLoopError` for tests that import directly from the submodule.
- `build_llm` wrapper validates model name prefix before delegating to `antcrew-engine`, independent of which engine version is installed.
- CI: `ANTCREW_SANDBOX=none` prevents host-Python-path-inside-Docker failure in test runner.

---

## [0.33.0] — 2026-07-09

### Added
- All 13 engine capabilities now exported from the `antcrew` root namespace: `Architect`, `BugFixer`, `CodeGenerator`, `CodeRegenerator`, `CodeReviewer`, `DependencyInstaller`, `DocGenerator`, `HitlReviewer`, `ReviewFixer`, `SpecExtractor`, `TaskPlanner`, `TeamExecutor`, `TestGenerator`, `TestRunner`. Previously only accessible via `antcrew.capabilities.*` or `antcrew_engine.*`.
- `MultiRepoStore` added to the `antcrew` root re-exports.

### Changed
- Dependency bumped to `antcrew-engine>=0.2.0` (adds `MultiRepoStore`, cache hit metrics, `--from-dir`/`--repo`/`--route` CLI flags, `_ensure_conftest`, `PYTHONPATH` injection).

### Fixed
- Stale test imports updated after Layer 1/2 separation — `_filter_python_delta`, `_is_retryable`, `_is_reasoning_model`, `_pick_fixture` now imported from `antcrew_engine.*`.
- `test_usage_summary_empty_when_no_calls` assertion updated to include `total_cache_read_tokens` and `total_cache_write_tokens` (added in antcrew-engine v0.2.0).
- Deleted `test_server.py` — `antcrew.server` module was removed in v0.15.0; server replaced by `antcrew-platform`.

---

## [0.32.0] — 2026-07-08 (engine)

### Engine — Layer 2 extracted to `antcrew-engine`; arch_text system-prompt caching

#### Layer separation

- **New package `antcrew-engine`** (`antcrew_engine.*`) — engine/, capabilities/, models/,
  testing/ and a standalone CLI (`antcrew-engine run / status`) extracted into their own
  installable package with no LangGraph dependency.
- **`antcrew.*` shims** — `antcrew.engine`, `antcrew.capabilities`, `antcrew.models` are now
  one-line re-exports from `antcrew_engine`; all existing import paths continue to work.
- **`antcrew.config.build_llm` delegates to `antcrew_engine.config.build_llm`** — antcrew no
  longer duplicates the LLM factory logic.
- **`EventBusBridge` DI** — accepts optional `on_event` callable; no runtime import of
  `antcrew.core.events` unless the fallback path is needed (backward-compatible).
- **`antcrew-platform` updated** — `engine_runner.py` imports directly from `antcrew_engine.*`
  instead of going through antcrew shims; `pyproject.toml` declares `antcrew-engine>=0.1.0`.

#### arch_text caching

- **System-prompt injection** — every capability moves architecture text from the user message
  into the system message (`## Project Architecture` section). Anthropic's `cache_control:
  ephemeral` now caches the architecture document across parallel CodeGenerator workers and
  BugFixer retries, eliminating redundant input token billing.
- **Truncation for peripheral capabilities** — `doc_generator`, `dependency_installer`,
  `review_fixer`, `test_generator` send only the first 100 lines of the architecture document;
  core capabilities (`code_generator`, `bug_fixer`, `code_regenerator`, `code_reviewer`,
  `task_planner`) receive the full document.

---

## [0.31.0] — 2026-07-08 (engine)

### Engine — HITL gate, token optimisation, context truncation, per-capability model routing

All changes in this release target the capability-driven engine (Layer 2: `antcrew engine`).

#### HITL before expensive operations

- **`Operator.pre_dispatch` hook** — caller-supplied `Callable[[Executor, ArtifactStore], bool]`
  executed between `decide()` and `execute()`; returning `False` raises `OperatorError(CANCELLED)`.
- **`--confirm-before <cost>`** CLI flag — prompts before any capability with cost weight ≥ threshold;
  shows the pending task list when `code_generator` is about to run.
- **Edit mode for `task_planner` HITL** — console prompt offers `(approve/reject/edit)`; edit opens
  `$EDITOR` (or `notepad` on Windows); the saved JSON is written back via `HitlReviewer` without an
  LLM re-run (new `edit` verdict with `new_content`).
- **Platform: `resolve_engine_review` accepts `new_content`** — `reviews.py` parses `body.edited`
  as JSON and forwards it so web UI edits unblock the engine with the modified artifact.
- **`TaskPlanner` reads rejection feedback** — reads `task_planner_feedback` artifact and prepends
  "Human review feedback:" to the LLM prompt on re-run (mirrors existing Architect behaviour).

#### Token optimisation

- **Prompt caching on by default** — CLI and engine runner both call `build_llm(model, prompt_caching=True)`;
  `--no-cache` disables globally.
- **Per-capability caching via YAML** — `prompt_caching: false` in a capability block overrides the
  global setting; `capability_models.yaml` template disables caching for Haiku capabilities
  (cheap model, 300 ms warmup not worth it) and enables it for Sonnet/Opus.
- **`_build_registry` propagates caching to per-capability LLMs** — previously, capabilities with a
  model override did not pass `prompt_caching` to `build_llm`; now always explicit.
- **BugFixer output compression** — `_compress_test_output()` extracts FAILED/ERROR/traceback lines
  instead of raw tail; `_extract_file_linenos()` + `_extract_context()` send ±40 lines around the
  failing line instead of the full file.
- **TestGenerator truncation** — source content capped at 150 lines; public interface is sufficient
  for test generation.

#### Capability model templates

- **`capability_models.yaml`** — balanced tier: Haiku (caching off) for planning, Sonnet (caching on)
  for code. ~60% cost reduction vs all-Sonnet.
- **`capability_models_quality.yaml`** — quality tier: Opus for code/review/bugfix, Sonnet for planning.

---

## [0.14.3] — 2026-07-04

### Fixed — Thread-safe EventBus

`EventBus` now uses a `threading.Lock` to guard all mutations and reads
of the handler registry. Previously, two concurrent `AsyncTeam*` runs in the
same process could corrupt each other's subscription lists.

The lock is released before handlers are called, so handlers that call
`bus.subscribe()` / `bus.unsubscribe()` do not deadlock.

### Added — CoherenceAgent `agent.start` / `agent.end`

`CoherenceAgent` is called after `app.invoke()` finishes, outside the
LangGraph supervisor, so `_make_evented_run()` never wrapped it. Both
`DevTeam` and `FullStackTeam` now emit:

```
agent.start  {agent_name: "coherence"}
agent.end    {agent_name: "coherence", duration_s, produced_keys: ["coherence_issues"]}
coherence.run {files_corrected, team?}
```

The `coherence.run` event is retained as a richer semantic event;
`agent.start/end` is the new observability pair consistent with all other agents.

### Added — Tests: feedback events, CoherenceAgent, thread-safety

18 new tests in `tests/test_events_v3.py`:

- `FeedbackLoop`: `feedback.start/round/end` on pass, retry, exhaustion;
  `run_id` propagated from state; `None` run_id still emits; event order.
- `run_test_feedback_loop`: `feedback.start/round/end`; `type` field is
  `"test"`; per-attempt round events.
- `CoherenceAgent`: `agent.start/end` emitted outside the LangGraph supervisor
  in `DevTeam`; `run_id` present on coherence agent events.
- `EventBus` thread-safety: 10 concurrent threads × 50 events each; concurrent
  `subscribe` + `unsubscribe`; no deadlock from handlers that call `subscribe`;
  isolated bus instances share no state.

---

## [0.14.2] — 2026-07-04

### Added — Event bus: full coverage of remaining run paths

`run_interactive()`, `FeedbackLoop`, `run_test_feedback_loop`, `FeatureTeam`,
`Pipeline`, and `Router` now emit events, completing event bus coverage across
every public run path in the framework.

**New event types:**

| Event | Emitted by | Payload |
|---|---|---|
| `feedback.start` | `FeedbackLoop`, `run_test_feedback_loop` | `{max_rounds, has_lint?}` |
| `feedback.round` | `FeedbackLoop`, `run_test_feedback_loop` | `{round, max_rounds, ok, type}` |
| `feedback.end` | `FeedbackLoop`, `run_test_feedback_loop` | `{rounds_used, success}` |
| `router.dispatch` | `Router` | `{label, request}` |

**Updated behaviour:**

- `run_interactive()` now emits `pipeline.start` / `pipeline.end` with
  `interactive: True`. On user rejection, `pipeline.end` includes
  `stopped_after: <agent_name>`.
- `FeedbackTeam.run()` injects `_run_id` into state so `FeedbackLoop`
  inherits and propagates the same run ID across all feedback events.
- `Pipeline.run()` emits `pipeline.start` with a `steps` list and
  `pipeline.end` as a meta-event above the individual team events.
- `bus.emit()` now accepts a shorthand overload:
  `bus.emit("type", {payload}, run_id=..., thread_id=...)` alongside the
  original `bus.emit(Event(...))` API.

**Complete event catalogue** (all carry `run_id` and `thread_id`):

`pipeline.start`, `pipeline.end`, `agent.start`, `agent.end`,
`kb.updated`, `coherence.run`, `router.dispatch`,
`feedback.start`, `feedback.round`, `feedback.end`

---

## [0.14.1] — 2026-07-04

### Fixed — `_trace_run_id` separation + event coverage for all teams

All team `run()` methods now correctly separate the event bus correlation
ID (`_run_id`, generated with `new_run_id()`) from the TraceLog run ID
(`_trace_run_id`, returned by `trace_log.begin_run()`). Previously they
shared the same variable, which caused TraceLog calls to fail when a
`TraceLog` was active on teams other than `DevTeam`.

Affected teams: `FullStackTeam`, `ResearchTeam`, `ContentTeam`,
`CustomTeam`, `Router`.

### Added — Event bus integration for all remaining teams

- `FullStackTeam.run()` — `pipeline.start/end`, `kb.updated`, `coherence.run`
- `ResearchTeam.run()` — `pipeline.start/end`
- `ContentTeam.run()` — `pipeline.start/end`
- `CustomTeam.run()` — `pipeline.start/end`, `agent.start/end` per sequential step
- `Pipeline.run()` — `pipeline.start/end` with `steps` list
- `Router.run()` — `router.dispatch` with route label

### Fixed — `FullStackTeam._build_agent_map()` bypass

`FullStackTeam.run()` was calling `self._supervisor.build(self._agents, ...)`
directly, bypassing `_build_agent_map()` and the `_KBProxy` per-agent KB
injection promoted to `InteractiveMixin` in v0.14.0. Fixed.

---

## [0.14.0] — 2026-07-04

### Added — Platform readiness: JSON serialization + stable ticket IDs

#### `RunResult.to_dict()` / `to_json()`

Pipeline state contains Pydantic models (PRD, Ticket, CodeArtifact, …)
that are not JSON-serializable. The platform layer needs to persist run
history in a database.

```python
result = team.run("Build JWT auth")
result.to_dict()            # JSON-safe dict; Pydantic models -> plain dicts
result.to_json(indent=2)    # JSON string
result.to_dict(include_private=True)  # includes _run_id, _kb_context, …
```

`_serialize()` is module-level and importable for custom use. Any
unrecognized type falls back to `str(obj)` — serialization never raises.

#### Deterministic ticket IDs

`PMAgent` previously assigned `TICKET-001`, `TICKET-002`, … — sequential
IDs that reset every run. The platform cannot upsert tickets when the same
ticket title gets a different ID on each run.

New: `_stable_ticket_id(prd_title, ticket_title, index)` computes
`TICKET-<SHA-256[:8]>` — a 8-char uppercase hex digest of the three inputs.
Same PRD + same ticket title always produces the same ID, across runs.

#### `_build_agent_map` promoted to `InteractiveMixin`

The `_KBProxy` logic that wraps agents with per-agent KB context was
previously a `DevTeam`-only override. Moved to `InteractiveMixin` so
`FullStackTeam` and all future teams inherit it automatically.

---

## [0.13.7] — 2026-07-04

### Added — Pipeline event bus (`antcrew.core.events`)

Zero-dependency, synchronous event bus for pipeline observability. Designed
as the primary integration point for the `antcrew-platform` dashboard layer
without coupling external code to pipeline internals.

```python
from antcrew import bus, Event, capture

# Subscribe to specific events
bus.subscribe("agent.end", lambda e: print(e.payload["agent_name"], e.payload["duration_s"]))

# Or capture all events in tests / one-shot scripts
with capture("pipeline.end") as events:
    team.run("Build JWT auth")
print(events[0].payload["artifact_summary"])
```

**Event catalogue**: `pipeline.start`, `pipeline.end`, `agent.start`, `agent.end`,
`kb.updated`, `coherence.run` — all carrying `run_id` and `thread_id` for correlation.

**Integration**: `Supervisor.build()` now wraps every agent node with `_make_evented_run()`
so `agent.start/end` events are emitted automatically — no agent code changed.
`DevTeam.run()` and `_SingleAgentTeam.run()` emit `pipeline.start/end`.
Every run receives a unique `_run_id` (12-char hex) in the initial state.

**Public API**: `from antcrew import bus, Event, EventBus, capture, new_run_id`

29 tests in `tests/test_events.py`.

---

## [0.13.6] — 2026-07-04

### Fixed — Sixth gap pass: KB pipeline correctness

#### `_KBProxy` — per-agent KB context in DevTeam pipelines

DevTeam previously set `_kb_context` once in the initial state using
`context_for_agent("backend_dev")` — a superset context shared by every node.
All the role-specific filtering added in earlier passes (`reviewer`, `pm`,
`doc_writer`, `business_analyst`) was never actually invoked in DevTeam.

New `_KBProxy` class wraps each agent at graph-build time and overrides
`_kb_context` in the state copy before delegating to `agent.run()`. Supervisor
only accesses `.run` and `.approval_required` — both forwarded by the proxy.

`DevTeam._build_agent_map()` builds the proxy map when a KB is configured;
returns `self._agents` unchanged when no KB is set (zero cost to existing
pipelines). `InteractiveMixin._build_agent_map()` provides the default
(`self._agents`) so `run_interactive()` also benefits when DevTeam overrides it.

#### `MinimalPipeline` now loads `ProjectKB` for single-agent task types

`project_kb_path` was forwarded to `DevTeam` for multi-agent paths but never
instantiated for `_SingleAgentTeam` (TEST, DOCS). The `--kb` flag was silently
ignored for those task types. Fixed: in the single-agent branch of `_build_team`,
`ProjectKB.load(project_kb_path)` is called and the live instance is passed to
`_SingleAgentTeam`. Load errors are caught and logged.

#### `_make_agents` registry now includes `DevOpsAgent` and `FrontendDevAgent`

Both agents were absent, meaning any `_PIPELINE_FLOWS` / `_AGENTS_FOR_TYPE`
extension using `"devops"` or `"frontend_dev"` would silently produce no agent
for those nodes.

---

## [0.13.5] — 2026-07-02

### Added — Fifth gap pass: complete universal KB coverage + KB injection tests

#### `BusinessAnalystAgent` reads `_kb_context`

The BA is the first node in the FEATURE pipeline (`business_analyst → pm → backend_dev → qa`).
It was the last agent still ignoring the Project Knowledge Base. Now `kb_ctx` is prepended
to `_SYSTEM + memory_ctx` so the BA sees existing endpoints/models before writing the PRD.

#### `DevOpsAgent` uses `_search_repo` and `_recall`

DevOps already received `_kb_context` in v0.13.4. Now it also queries the repo for existing
infra files (`Dockerfile`, `docker-compose.yml`, `.github/`) before generating new ones,
and recalls past deployment decisions from memory — same pattern as `BackendDevAgent`.

#### `tests/test_kb_context_injection.py` — regression guard for KB injection

7-test module that verifies every pipeline agent (BusinessAnalyst, PM, QA, Reviewer,
DocWriter, DevOps, + regression guard for empty KB) injects `_kb_context` into the LLM
system prompt. Each test uses a sentinel string and checks `llm.system.call_args_list`.
A future refactor that silently drops `kb_ctx` from the prompt concatenation will fail.

---

## [0.13.4] — 2026-07-02

### Fixed — Fourth gap pass: universal KB context, complete initial state, `minimal` in choices

#### All pipeline agents now read `_kb_context` from state

Previously only `BackendDevAgent` and `FrontendDevAgent` injected the ProjectKB context
into their LLM prompts. The five remaining pipeline agents now do the same:

| Agent | Injection point |
|---|---|
| `QAAgent` | prepended to `system_prompt` before repo search |
| `ReviewerAgent` | prepended to `_SYSTEM` before memory + repo context |
| `PMAgent` | prepended to `_SYSTEM` before recall context |
| `DocWriterAgent` | prepended to `_SYSTEM` before `context_parts` join |
| `DevOpsAgent` | prepended to `_SYSTEM` before `context` |

Pattern: `kb_ctx = str(state.get("_kb_context") or "")` — empty string when no KB is
configured, so existing pipelines without `ProjectKB` are unaffected.

#### `_SingleAgentTeam` initial state mirrors `DevTeam._initial_state`

Eight keys were missing from `_SingleAgentTeam.run()`'s initial state dict:
`prd`, `tickets`, `test_results`, `review`, `devops_artifacts`, `doc_artifacts`,
`research_document`, `content_piece`.

`DocWriterAgent` and `DevOpsAgent` read these keys via `.get()` so no `KeyError` was
raised, but middleware that checks `key in state` (rather than `state.get(key)`) would
have silently skipped sections. All slots are now initialised to `None`, matching the
full-pipeline DevTeam state shape.

#### `'minimal'` added to `_TEAM_CHOICES`

`antcrew run --team minimal` worked correctly but `minimal` was absent from
`_TEAM_CHOICES`, so it was missing from `--help` output and error messages that list
valid choices.

---

## [0.13.3] — 2026-07-02

### Added — Third gap pass: CLI visibility, MinimalPipeline context, KB for all roles

#### `_print_state` shows v0.13.x metadata (Gap 1 + Gap 4)

- `antcrew run` output now surfaces four new state keys after every pipeline:
  - `task=<type>` — task type chosen by MinimalPipeline
  - `coherence=N corrected / no issues` — CoherenceAgent result
  - `lint=ok / failed` — lint pre-pass result
  - `feedback=ok (N rounds) / failed after N rounds` — FeedbackLoop result
  - Line is suppressed when none of these keys are present (existing pipelines unaffected).
- `minimal` team added to the `("dev", "fullstack")` branch so `antcrew run --team minimal`
  now renders PRD, tickets, code artifacts, test files, test results, and code review.

#### `MinimalPipeline` builds `SymbolIndex` from `repo_path` for single-agent tasks (Gap 2)

- `MinimalPipeline.__init__` stores `repo_path` from `**kwargs`.
- In `_build_team`, when the task resolves to a single-agent path (TEST or DOCS) and
  `repo_path` is set, `SymbolIndex.build([repo_path])` is called and the index is
  attached to the agent before `_SingleAgentTeam` wraps it.  Build errors are caught
  and logged — never crash the pipeline.

#### `antcrew run --task-type` / `-T` (Gap 3)

- `antcrew run "..." --team minimal --task-type fix` forces a specific task type.
- Valid values: `fix`, `refactor`, `feature`, `test`, `docs`.  Default: `auto`.
- `_build_team()` in `_shared.py` now has a `minimal` branch that instantiates
  `MinimalPipeline` with all v0.13.x options (`--lint-cmd`, `--coherence`, `--kb`,
  `--repo-index`, `--task-type`) working together.

#### `ProjectKB.context_for_agent()` now serves `reviewer`, `pm`, `doc_writer` (Gap 5)

- Previously these roles always received an empty string from the KB.
- New role → section mapping:
  - `reviewer`: dependencies, endpoints, models, services
  - `pm`: endpoints, models, services
  - `doc_writer`: endpoints, models, services
  - `business_analyst`: tech_stack only (works from scratch, not from the existing KB)
- 5 new tests in `TestContextForAgentRoles`.

### Tests

- 2274 passing (up from 2269); 18 new tests across 2 files.

---

## [0.13.2] — 2026-07-02

### Added — Second gap pass: frontend context, FEATURE QA, feedback everywhere, labeled symbols

#### FrontendDevAgent uses SymbolIndex and ProjectKB (Gap 1)

- `FrontendDevAgent.run()` now reads `_kb_context` from state and calls
  `self.symbol_index.context_for(topics)` if an index is attached, mirroring
  `BackendDevAgent`.  Both are prepended to the prompts used for the planning and
  file-generation phases.
- In `FullStackTeam`, `DevTeam`, and `MinimalPipeline`, the symbol index is attached
  to all agents — `FrontendDevAgent` now benefits from the same structural context
  `BackendDevAgent` already had.

#### MinimalPipeline FEATURE includes QA (Gap 2)

- `_AGENTS_FOR_TYPE[FEATURE]` updated from `["business_analyst", "pm", "backend_dev"]`
  to `["business_analyst", "pm", "backend_dev", "qa"]`.
- `_PIPELINE_FLOWS[FEATURE]` gains a `("backend_dev", "qa")` edge.
- FEATURE tasks now produce test artifacts.  Only single-agent tasks (TEST, DOCS)
  skip QA.

#### `_SingleAgentTeam` forwards `feedback_rounds` to the feedback loop (Gap 3)

- `_SingleAgentTeam` now accepts `runner`, `feedback_rounds`, `lint_cmd`, `work_dir`,
  and `max_error_chars` from `MinimalPipeline._team_kwargs`.
- When `runner` is provided and `feedback_rounds > 0`, `run_test_feedback_loop()` runs
  after the primary agent with a fresh `BackendDevAgent` as fixer.
- If the feedback loop raises, it is caught and logged — never crashes the pipeline.

#### `antcrew test` supports `--feedback-rounds` (Gap 4)

- `antcrew test state.json --feedback-rounds 3` re-runs failed tests with a
  `BackendDevAgent` fix cycle before giving up.
- `--lint-cmd TEXT` adds a static check pre-pass each round.
- `--model TEXT` selects the model for the fix agent (default: `claude`).
- Exit code 0 when feedback loop succeeds; exit 1 when still failing.

#### `SymbolIndex.context_for()` labels each symbol's language (Gap 5)

- Every line in the context block now carries a `[Language]` prefix:
  `[Python] def login(...)`, `[TypeScript] class AuthService`.
- New `_ext_to_lang(file_path)` helper maps 13 extensions to display names.
- Prevents agents from confusing Python `from module import name` with TypeScript
  `import { name } from './module'` when reading codebase context.

### Tests

- 2269 passing (up from 2266); 16 new tests across 3 files.

---

## [0.13.1] — 2026-07-02

### Added — Gap fixes: TS symbols, CLI flags, single-agent pipeline, language-aware coherence

#### TypeScript/JavaScript symbol extraction (Gap 2)

- `SymbolIndex.build()` now scans `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` via regex in
  addition to Python (AST).  Extracted: exported functions (including arrow functions),
  classes (with public methods), interfaces, and type aliases.
- `QAAgent._extract_symbols_context()` calls `_ts_symbols()` for TS/JS files, so the agent
  sees the actual exported names before generating Vitest/Jest tests.
- `_ts_symbols(source)` public helper available in `antcrew.agents.qa`.
- 6 new tests in `test_symbol_index.py` (`TestTypeScriptIndex`); 3 new tests in
  `test_qa_agent.py` (`test_ts_file_*`).

#### CLI flags: `--lint-cmd`, `--coherence`, `--kb` (Gap 3)

- `antcrew run` now exposes three new flags without requiring a YAML config:
  - `--lint-cmd TEXT` — static check pre-pass before tests each feedback round
    (e.g. `antcrew run "..." --feedback-rounds 3 --lint-cmd "ruff check ."`)
  - `--coherence` — enable CoherenceAgent post-generation pass
  - `--kb PATH` / `--project-kb PATH` — path to project knowledge base JSON
- All three are forwarded through `_build_team()` to `DevTeam` and `FullStackTeam`.

#### MinimalPipeline: proper single-agent wrapper (Gap 4)

- Replaced `_SingleNodeSupervisor` / `_SingleNodeApp` (fake LangGraph duck-types with a
  broken `get_state()`) with `_SingleAgentTeam`.
- `_SingleAgentTeam.run()` calls the agent directly, builds a proper initial state, merges
  the result, and returns a real `RunResult` — no LangGraph involved for TEST or DOCS tasks.
- KB injection and KB update/save are handled inside `_SingleAgentTeam` when a `ProjectKB`
  is attached.

#### Language-aware CoherenceAgent (Gap 5)

- `CoherenceAgent` now explicitly tells the LLM which languages are present and enforces
  language-boundary rules: Python uses `from module import name`; TypeScript/JavaScript uses
  `import { name } from './module'` — they must never be mixed.
- New `_lang_summary(artifacts)` helper produces a one-line header like
  *"Languages present: 2 Python, 1 TypeScript — apply each language's own import conventions"*.
- `_build_context()` now returns `(files_json, lang_summary)`; the language header is placed
  before the file list so the LLM reads it first.
- 4 new tests in `test_coherence_agent.py` (`TestBuildContext.test_lang_summary_*`).

### Fixed

- `CoherenceAgent` was silently misapplying Python import rules to TypeScript files in
  mixed-language projects (now guarded by explicit per-language instructions).
- `MinimalPipeline` TEST/DOCS branches would crash if `run_interactive()` was called on the
  returned team (fake `get_state()` returned an empty struct-like object).

### Tests

- 2266 passing (up from 2255 in v0.13.0); 13 new tests for the 4 gap fixes.

---

## [0.13.0] — 2026-06-30

### Added — Depth improvements: smarter code generation, richer feedback, structured memory

#### Lint pre-pass in FeedbackLoop (commit 1)

- `run_test_feedback_loop()` now accepts `lint_cmd` and `work_dir`.  When given,
  a static check (ruff, mypy, eslint, etc.) runs before the test suite each round.
  Lint failures are fed to `BackendDevAgent.fix_test_failures()` directly —
  catching import errors and type errors in milliseconds, not test-run time.
- `DevTeam(lint_cmd=..., work_dir=...)` and `FullStackTeam(...)` propagate both params.
- State key written: `lint_ok` (bool) when `lint_cmd` is provided.
- YAML keys: `lint_cmd` (list or string), `work_dir`.

#### QAAgent code-first test generation (commit 2)

- `QAAgent` now extracts the public API of each source file via AST before generating
  tests.  Function names with argument names and public class names are prepended to
  the LLM context so the agent imports exactly what exists.
- System prompt updated: agents are instructed to test functions present in the code,
  not hypothetical signatures from the ticket description.
- `_extract_symbols_context(source, file_path)` and `_python_symbols(source)` public
  helpers available in `antcrew.agents.qa`.

#### CoherenceAgent — cross-file consistency pass (commit 3)

- New `CoherenceAgent` receives all generated code files in one LLM call and fixes:
  broken imports (wrong name or path), signature mismatches, type inconsistencies.
- Only touches what needs fixing; preserves all other code.
- `DevTeam(enable_coherence=True)` and `FullStackTeam(enable_coherence=True)` run the
  pass between pipeline completion and test runner — before the feedback loop.
- YAML key: `enable_coherence: true`.
- State key written: `coherence_issues` (list of changed file paths).
- `antcrew.CoherenceAgent` exported from top-level package.

#### AST Symbol Index (commit 4)

- `SymbolIndex.build(paths)` scans Python files via `ast` (no LLM, no vectors)
  and indexes all functions, methods, and classes with exact signatures, argument
  names, return types, docstrings, and source locations.
- `query_function(name)` / `query_class(name)`: O(1) exact lookup.
- `context_for(topics)`: fuzzy-match topics → compact snippet for LLM injection.
- `BaseAgent.symbol_index: Optional[SymbolIndex]` attribute (like `repo_index`).
- `DevTeam(repo_path=...)` now builds both a semantic `RepoIndex` and an AST
  `SymbolIndex`, attaching both to all agents automatically.
- `BackendDevAgent.run()` prepends symbol context alongside `_kb_context` so agents
  know what names, signatures, and types exist before writing imports.
- `antcrew.core.symbol_index.SymbolIndex` available for direct use.

#### Project Knowledge Base (commit 5)

- `ProjectKB` stores structured records across runs: API endpoints (path, method,
  handler), data models (name, fields), service classes, and dependencies.
  Built by AST-parsing generated code — not relying on the LLM to recall prior work.
- `update_from_state(state)`: extracts from FastAPI/Flask decorators, Pydantic/SQLAlchemy
  models, service classes, and requirements.txt / pyproject.toml.
- `save()` / `load(path)`: JSON persistence; survives restarts.
- `context_for_agent(name)`: compact KB block filtered per agent role for LLM injection.
- `DevTeam(project_kb_path="./.antcrew/kb.json")`: loads KB at startup, injects
  `context_for_agent("backend_dev")` into every run's initial state via `_kb_context`,
  then updates and saves after each run.
- YAML key: `project_kb_path`.
- `antcrew.ProjectKB` exported from top-level package.

#### Minimal pipelines by task type (commit 6)

- `classify_task(request)`: fast rule-based classifier (no LLM call).
  - fix → bug|broken|failing|crash|error|exception|traceback|…
  - refactor → refactor|restructure|cleanup|rename|extract|…
  - test → test|pytest|coverage|mock|fixture|…
  - docs → docs|documentation|readme|changelog|docstring|…
  - feature → build|create|implement|add|endpoint|api|… (fallback default)
- `classify_task_llm(request, llm)`: single LLM call, falls back to rules on error.
- `MinimalPipeline`: DevTeam-like runner that selects the narrowest pipeline for the
  classified task type.  A bugfix runs only BackendDevAgent + QA; a docs update runs
  only DocWriterAgent.  Accepts `task_type=` to force a type, `use_llm_classifier=True`
  for LLM classification, and all DevTeam kwargs.
- YAML: `team: minimal`, with optional `task_type: fix|refactor|feature|test|docs`
  (default: `auto`) and `use_llm_classifier: true`.
- `antcrew.TaskType`, `antcrew.classify_task`, `antcrew.MinimalPipeline` exported.

### Tests

- 74 new tests across 6 test files: `test_qa_agent.py` (10), `test_coherence_agent.py`
  (10), `test_symbol_index.py` (20), `test_project_kb.py` (16), `test_task_classifier.py`
  (18).  Total test count: 2255 passing.

---

## [0.12.0] — 2026-06-29

### Added — Auto-routing, Operators, FeatureTeam, ArtifactContract, Async wrappers

#### Router + DirectAgent (auto-routing)

- `Router` — dispatches requests to the right team or agent based on classification; injects `_route` into state.
- `LLMClassifier` — asks an LLM to classify a request into a route label; graceful fallback on error.
- `RuleClassifier` — regex-based instant classification (no LLM call, instant).
- `DirectAgent` — single LLM call returning `RunResult`; composable anywhere a team is expected.
- YAML `team: auto` — 2-route setup (simple → DirectAgent, complex → configurable team).
- YAML `team: routed` — full control: n routes, `classifier: llm|rule`, `default_route:`, `rules:`.
- `classifier_model:` key — use a cheaper model (e.g. Groq) for classification only.
- `Router` integrates with `TraceLog` — routing decisions appear in `antcrew trace` output.
- All new classes exported from `antcrew` top-level.

#### Operators — state transforms for CustomTeam

- `BaseOperator` — lightweight transform; `run(state) -> dict` interface, composable as pipeline steps.
- Built-in operators: `RenameOp`, `CopyOp`, `DropOp`, `SetOp`, `MergeOp`, `MapOp`.
- `_DELETE` sentinel — returned by operators to remove a key from state without `del`.
- `_apply_patch(state, patch)` — handles `_DELETE` cleanly in `CustomTeam` step execution.
- `build_operator(cfg)` — parses YAML operator dicts (`rename`, `copy`, `drop`, `set`, `merge`, `map`).
- All existing `CustomTeam` state updates now go through `_apply_patch`.
- All operator classes and `build_operator` exported from `antcrew` top-level.

#### ArtifactContract — type-safe state access

- `ArtifactContract(key, ModelClass).extract(state)` — reads a typed artifact; raises `ContractError` on missing/wrong type.
- `coerce_model(raw, cls)` — converts dict or model instance to typed model.
- `coerce_list(state, key, cls)` — coerces each item in a list key; leaves invalid items in place.
- All built-in agents (PM, BackendDev, Editor, Copywriter, Reviewer, QA, DevOps, DocWriter, FrontendDev, Business, SprintPlanner) migrated to use `ArtifactContract`/`coerce_list` for all state reads.
- `coerce_model` and `coerce_list` exported from `antcrew` top-level.

#### validate_agent_dag

- `validate_agent_dag(agents, initial_keys, strict)` — checks that each agent's `consumes` keys are produced by prior agents.
- `strict=True` (default): raises `ValueError` on first violation.
- `strict=False`: returns list of violation strings.
- `CustomTeam(validate_dag=True)` — auto-calls validator at construction time.
- YAML key `validate_dag: true` in custom team configs.
- `validate_agent_dag` exported from `antcrew` top-level.

#### CLI: `antcrew dag` command

- New `antcrew dag <config>` command — validates and displays the agent dependency graph as a rich table.
- Highlights missing dependencies in red; exits 1 on violations (disable with `--no-strict`).
- Supports `custom`, `dev`, and `fullstack` team configs.

#### CLI: new team types

- `_TEAM_CHOICES` now includes `custom`, `feature`, `auto`, `routed`, `direct`.
- `--team feature` and `--team direct` work from the CLI.
- `--team auto` / `--team routed` / `--team custom` emit a clear error directing users to `--config`.
- `_print_state` handles `auto`, `routed`, `feature`, `direct` output; shows `_route` when set.

#### Async wrappers

- `AsyncCustomTeam`, `AsyncFeatureTeam`, `AsyncRouter` — composition-based async wrappers.
- All delegate `run()` to `asyncio.to_thread`; expose `run_sync()` for sync contexts.
- Attribute access falls through to the inner team via `__getattr__`.
- All three exported from `antcrew` top-level alongside existing `AsyncDevTeam` etc.

#### FeatureTeam (promoted)

- `FeatureTeam` now available via CLI (`--team feature`) and YAML (`team: feature`).

#### Testing utilities

- `SequencedLLM` exported from `antcrew` top-level (was `antcrew.testing` only).

### Changed

- `CustomTeam` step execution uses `_apply_patch` for all state updates (no functional change for agents that don't use `_DELETE`).
- `config.py`: extracted `_build_team_cfg(cfg, base_dir)` from `load()` to enable recursive team building for `team: auto/routed`.

---

## [0.11.11] — 2026-06-29

### Added — Context keys: per-step state filtering

Each step can now declare `context_keys:` to receive only the relevant slice
of the shared state instead of the full accumulated dict.  Prevents context
bloat in long pipelines and makes agent dependencies explicit.

#### How it works

The full pipeline state grows with every step: after 5 agents each writing
1 000 tokens, agent 6 would receive 5 000 tokens of prior context it may not
need.  With `context_keys:`, agent 6 declares exactly what it needs — the
LLM sees only those keys (plus `request`, always available).

The gate still runs on the full merged state — filtering is agent-scoped only.

#### YAML — CustomTeam steps

```yaml
steps:
  - name: analyst
    system_prompt: "Analyse the codebase."
    output_key: analysis

  - name: planner
    system_prompt: "Create a plan based on: {analysis}"
    output_key: plan
    context_keys: [analysis]      # only sees analysis + request

  - name: backend
    system_prompt: "Implement: {plan}"
    output_key: code
    context_keys: [plan]          # only sees plan + request; analyst output hidden

  - name: reviewer
    system_prompt: "Review {code} against plan {plan}"
    output_key: review
    context_keys: [plan, code]    # sees both plan and code
```

String shorthand also works: `context_keys: plan` (single key).

#### Python — CustomTeam

```python
from antcrew.teams.custom_team import CustomTeam, _Step

# Direct _Step construction:
step = _Step(agent=my_agent, context_keys=["prd", "code"])

# Dict config:
team = CustomTeam(
    steps=[
        {"name": "builder", "system_prompt": "Build: {prd}", "output_key": "code",
         "context_keys": ["prd"]},
    ],
    llm=llm,
)
```

#### TemplateAgent — standalone

```python
agent = TemplateAgent(
    {"name": "executor", "system_prompt": "Execute: {plan}", "context_keys": ["plan"]},
    llm=llm,
)
```

Or in a YAML agent file:

```yaml
name: executor
system_prompt: "Execute the plan: {plan}"
context_keys:
  - plan
```

#### `_filter_state(state, context_keys)` — public helper

```python
from antcrew.teams.custom_team import _filter_state

filtered = _filter_state(state, ["prd", "plan"])
# → {"request": ..., "prd": ..., "plan": ...}
```

#### 21 new tests

`TestFilterState` (6), `TestCustomTeamContextKeys` (7),
`TestTemplateAgentContextKeys` (7), `TestContextKeysYAMLIntegration` (1) —
in `tests/test_context_keys.py`.

---

## [0.11.10] — 2026-06-29

### Added — Structured artifact contracts

Agents now speak in typed Pydantic models instead of unstructured strings.
An `ArtifactContract` declares the state key and model class for a pipeline
artifact; `TemplateAgent` can validate and parse LLM output directly into a
named model with the new `output_schema:` config key.

#### `resolve_artifact_schema(name) -> type[BaseModel]`

Maps a string name to a Pydantic model class:

```python
from antcrew import resolve_artifact_schema, PRD

cls = resolve_artifact_schema("PRD")          # → PRD
cls = resolve_artifact_schema("antcrew.core.artifacts.PRD")  # dotted path
```

Built-in schemas: `PRD`, `CodeArtifact`, `TestArtifact`, `CodeReview`,
`ResearchDocument`, `DevOpsArtifact`, `DocumentationArtifact`, `ContentPiece`,
`CodebaseAnalysis`, `Ticket`.

#### `ArtifactContract`

Typed state accessor that handles dict/string/instance deserialization:

```python
from antcrew import ArtifactContract, PRD

prd_contract = ArtifactContract("prd", PRD)

# Producing agent:
def run(self, state):
    prd = PRD(title="...", summary="...", goals=["..."])
    return prd_contract.inject(prd)          # → {"prd": {...dict...}}

# Consuming agent:
def run(self, state):
    prd = prd_contract.extract(state)        # → PRD instance, validated
    print(prd.title)
```

`inject()` stores as a plain dict (JSON-serializable for checkpointers).
`extract()` accepts model instance, dict, or JSON string — transparent to how the value was stored.

#### `output_schema:` in TemplateAgent

```yaml
steps:
  - name: pm
    system_prompt: |
      Write a Product Requirements Document for: {request}
      Return JSON matching the PRD schema.
    output_key: prd
    output_schema: PRD            # built-in name
    output_parse_retries: 2       # retry if schema validation fails
```

The LLM is called with `json_mode=True`; output is validated against `PRD`
and stored as `model_dump()` (dict) in state. A downstream step can then call
`ArtifactContract("prd", PRD).extract(state)` to get a typed instance.

If validation fails after all retries, the step raises (contract is strict).

#### `ContractError`

Raised by `ArtifactContract.extract()` and `inject()` on type mismatches or
missing keys — distinct from `GateError` (output quality) and `ValidationError`
(Pydantic schema).

#### 35 new tests

`TestResolveArtifactSchema` (9), `TestArtifactContract` (11),
`TestTemplateAgentOutputSchema` (8), `TestPublicExports` (7) —
in `tests/test_artifact_contracts.py`.

---

## [0.11.9] — 2026-06-29

### Added — Feedback loop: execute-validate-retry for code-generating agents

Closes the most critical gap in the original architecture: generated code was
never executed. The feedback loop runs a real command (pytest, mypy, etc.)
against the files the agent wrote, and injects the error output back into the
agent's context for self-correction — up to a configurable round budget.

#### `FeedbackRunner`

```python
from antcrew import FeedbackRunner

runner = FeedbackRunner(
    ["pytest", "-x", "--tb=short"],
    work_dir="./src",
    timeout=60.0,
)
result = runner.run()   # FeedbackResult(ok, output, returncode, duration_ms)
```

Captures stdout + stderr, handles timeouts gracefully, supports any shell
command (not just pytest).

#### `FeedbackLoop`

```python
from antcrew import FeedbackLoop

loop = FeedbackLoop(runner=runner, max_rounds=3)
final_state = loop.run(agent.run, {"request": "Add JWT auth"})
# final_state["feedback_ok"]           → True if validation passed
# final_state["feedback_rounds_used"]  → how many rounds it took
```

On failure: injects `_feedback_error` (truncated command output) and
`_feedback_round` (round number) into state.  `FeatureAgent.run()` reads these
keys and prepends a structured error block to the user message so the LLM can
diagnose and fix the issue.

#### `FeatureTeam` — feedback shorthand

```python
from antcrew import FeatureTeam, AnthropicModel

team = FeatureTeam(
    llm=AnthropicModel(),
    project_dir="./src",
    max_feedback_rounds=3,
    validate_cmd=["pytest", "-x", "--tb=short"],
)
result = team.run("Add JWT authentication to the REST API")
print(result.state["feedback_ok"])           # True
print(result.state["feedback_rounds_used"])  # 1, 2, or 3
```

When `max_feedback_rounds=0` (default) or `validate_cmd` is absent, the loop
is disabled and behaviour is identical to v0.11.8.

#### YAML config

```yaml
team: feature
model: claude-sonnet-4-6
project_dir: ./src
max_tool_steps: 12
feedback_rounds: 3
validate_cmd: ["pytest", "-x", "--tb=short"]
validate_timeout: 60
max_cost_usd: 3.0
```

#### 32 new tests

`TestFeedbackResult` (4), `TestFeedbackRunner` (8), `TestFeedbackLoop` (9),
`TestFeatureTeamFeedback` (6), `TestConfigFeedbackYAML` (2),
`TestPublicExports` (3) — in `tests/test_feedback.py`.

---

## [0.11.8] — 2026-06-29

### Added — Feature Agent (vertical-slice single-context agent)

Introduces `FeatureAgent` and `FeatureTeam`: a single LLM context that owns
a complete feature end-to-end using tools, instead of splitting work across
role-based agents (PM → backend_dev → frontend_dev) whose contexts never overlap.

#### New tools

| Tool | Description |
|---|---|
| `WriteFileTool(root, allow_create_dirs)` | Write content to a file. Input: `path\n---\ncontent`. Blocks path traversal outside `root`. |
| `ListDirTool(root, max_depth, max_files)` | List directory tree. Skips `__pycache__`, `.git`, `node_modules`, etc. |

Both exported from `antcrew` top-level.

#### `FeatureAgent`

```python
from antcrew import FeatureAgent, AnthropicModel

agent = FeatureAgent(
    AnthropicModel(),
    project_dir="./src",      # all file I/O scoped here
    max_tool_steps=12,        # default 10
)
result_dict = agent.run({"request": "Add JWT auth", "plan": "..."})
# result_dict["feature_output"]  → agent summary
# result_dict["files_written"]   → list of paths written
```

Default tool stack: `read_file` → `write_file` → `list_dir` → `execute_code`.
Pass `extra_tools=[...]` to extend.

#### `FeatureTeam`

Thin wrapper with the same `run(request) -> RunResult` interface as DevTeam:

```python
from antcrew import FeatureTeam, AnthropicModel

team = FeatureTeam(llm=AnthropicModel(), project_dir="./src", max_cost_usd=2.0)
result = team.run("Add JWT authentication to the REST API")
print(result.state["files_written"])
```

#### YAML config

```yaml
team: feature
model: claude-sonnet-4-6
project_dir: ./src
max_tool_steps: 12
max_cost_usd: 2.0
```

```bash
antcrew run "Add JWT auth" --config feature.yaml
```

#### Agent registry

`feature` is now a first-class registry entry, usable in any `agents:` block.

#### 32 new tests

`TestWriteFileTool` (6), `TestListDirTool` (5), `TestFeatureAgent` (9),
`TestFeatureTeam` (5), `TestConfigFeatureTeam` (3), `TestPublicExports` (4) —
in `tests/test_feature_agent.py`.

---

## [0.11.7] — 2026-06-29

### Added — Workflow-as-primary-API: gates in YAML + `parse_gate`

Gates defined in code (v0.11.6) can now be expressed entirely in YAML, making
`agentteam.yaml` the single source of truth for a complete pipeline including
verification logic. No Python required.

#### `parse_gate(raw) -> BaseGate` (new public function)
Parses a gate from a YAML value — string shorthand or full dict:

```yaml
# String shorthand: "type:field"
gate: "non_empty:prd"
gate: "python_syntax:code_artifacts"
gate: "json:payload"

# Full dict (all options):
gate:
  type: non_empty
  field: prd
  min_length: 100

# Composable:
gate:
  type: all
  gates:
    - type: non_empty
      field: prd
    - type: python_syntax
      field: code_artifacts
```

Exported from `antcrew` top-level: `from antcrew import parse_gate`.

#### `gates:` in flow YAML (`agentteam.yaml`)
```yaml
team: dev
model: claude
flow:
  - [pm, backend_dev]
  - [backend_dev, qa]
gates:
  pm: "non_empty:prd"
  backend_dev: "python_syntax:code_artifacts"
```
`config.load()` now parses the `gates:` key and passes them to `Supervisor`.

#### `gate:` per step in `CustomTeam` YAML
```yaml
team: custom
model: claude
steps:
  - name: planner
    system_prompt: "Create a numbered plan."
    output_key: plan
    gate: "non_empty:plan"
  - name: executor
    system_prompt: "Execute: {plan}"
    output_key: result
    gate:
      type: non_empty
      field: result
      min_length: 50
```
Gate fires after the step writes its output key; `GateError` stops the
pipeline with the same structured diagnostic as v0.11.6.

#### 17 new tests
`TestCustomTeamGates` (5), `TestParseGate` (9), `TestConfigGatesYAML` (3) —
in `tests/test_custom_team.py`.

---

## [0.11.6] — 2026-06-29

### Added — Verification gates (`antcrew.core.gates`)

Gates run **after** an agent completes and verify its output before the next
agent starts. A failing gate raises `GateError` and stops the pipeline with a
clear diagnostic instead of silently propagating bad output downstream.

#### New module: `antcrew/core/gates.py`
- `BaseGate` — abstract base; subclass and implement `check(state) -> GateResult`.
- `GateResult(passed, message, field=None)` — frozen dataclass returned by every gate.
- `GateError(gate_name, message, field=None)` — exception with structured attributes.
- **Built-in gates:**
  - `NonEmptyGate(field, min_length=1)` — field must be non-None and non-empty.
  - `PythonSyntaxGate(field)` — every Python snippet (plain or fenced) in the field
    must parse without `SyntaxError`. Handles `str`, `list[str]`, and lists of
    objects with a `.content` attribute (e.g. `CodeArtifact`).
  - `JsonGate(field)` — field must be valid JSON.
  - `SchemaGate(field, model)` — field must validate against a Pydantic v2 model.
- **Composable gates:**
  - `AllGate(*gates)` — passes only when all sub-gates pass (short-circuits).
  - `AnyGate(*gates)` — passes when at least one sub-gate passes.

#### `Supervisor` changes
- `Supervisor(flow, gates={})` — optional `gates` dict mapping agent names to `BaseGate`.
- For each gated agent, the node's `run` function is wrapped so the gate fires
  on the merged state after the agent writes its output. Zero overhead when no
  gates are configured.

#### CLI changes
- `GateError` is caught in `antcrew run` and displayed with the gate name,
  field, and a plain-English hint about what failed.

#### All gates are top-level exports
```python
from antcrew import NonEmptyGate, PythonSyntaxGate, JsonGate, SchemaGate, AllGate, AnyGate
```

#### Usage example
```python
from antcrew import DevTeam, Supervisor, NonEmptyGate, PythonSyntaxGate

supervisor = Supervisor(
    flow=[("pm", "backend_dev"), ("backend_dev", "qa")],
    gates={
        "pm":          NonEmptyGate("prd"),
        "backend_dev": PythonSyntaxGate("code_artifacts"),
    },
)
team = DevTeam(supervisor=supervisor)
team.run("Build authentication module")
```

#### 44 new tests (`tests/test_gates.py`)
All gate types, composables, `Supervisor` integration (pass / fail / mixed),
`AllGate` short-circuit, `AnyGate` partial pass, and CLI error formatting.

---

## [0.11.5] — 2026-06-29

### Added — Full prompt/response tracing (`--full-trace`)

**Problem:** `TraceLog` stored only 300-character snippets, making it impossible
to understand *why* an agent produced a given output.

**Solution:** opt-in full-text capture with zero storage overhead when disabled.

#### `TraceLog` changes
- `TraceLog(db, full_trace=True)` — when set, stores the complete system prompt
  and LLM response for every call in new `prompt_full` / `response_full` columns.
- `record_call()` now accepts `prompt_full` and `response_full` kwargs and returns
  the integer primary key of the inserted row (previously returned `None`).
- `get_call_detail(call_id: int)` — fetch a single call by its primary key,
  including full text when available.
- **Schema migration** — existing DBs gain `prompt_full` / `response_full` columns
  automatically on first open (idempotent `ALTER TABLE ADD COLUMN`).
- Snippet columns (`prompt_snippet`, `response_snippet`) are always populated as
  before; when `full_trace=False` (default) the full columns remain empty.

#### CLI changes
- `antcrew run … --full-trace` — enable full prompt/response capture.
- `antcrew trace <db> --run <id> --show-call N` — display the complete prompt and
  response for the Nth agent call (1-indexed) in a Rich panel. Falls back to
  snippets with a warning when `--full-trace` was not used.
- Detail view now shows a hint: either `--show-call N` is available or it tells
  you to re-run with `--full-trace`.

#### 10 new tests (`tests/test_trace.py`)
`test_full_trace_flag_stores_complete_text`, `test_full_trace_off_by_default`,
`test_record_call_returns_int_id`, `test_get_call_detail_returns_full_text`,
`test_get_call_detail_returns_none_for_missing`, `test_migration_adds_columns_to_existing_db`,
`test_cli_show_call_full_trace`, `test_cli_show_call_out_of_range`,
`test_cli_show_call_requires_run_or_thread`, `test_cli_trace_detail_hints_when_no_full_trace`.

---

## [0.11.4] — 2026-06-29

### Fixed
- **`instantiate_agent()` now passes `max_cost_usd` from per-agent YAML config.**
  Add `max_cost_usd: 0.50` under any agent block in `agentteam.yaml` to cap that
  agent's spend independently of the team-level cap.
- **`export_cmd.py` removed** — the file became a stub after the serializers were
  merged into `trace_cmd.py`. The dead import in `cli/__init__.py` is gone too.
  The serializers (`_to_json`, `_to_csv`) now live in `trace_cmd.py` where they
  are used.

### Tests
- **16 streaming-retry tests** in `test_streaming_retry.py`:
  - Anthropic (4): connection error → retry, timeout → retry 2×, raises after
    max retries, tokens received correctly on success.
  - OpenAI (3): timeout + 429 → retry, raises after max retries. *(skipped when
    `openai` package not installed)*
  - Groq (3): 429 → retry, timeout → retry, raises after max retries.
  - Gemini (3): connection error → retry, timeout → retry, raises after max retries.
  - `instantiate_agent` (3): `max_cost_usd` from cfg, absent → `None`, zero allowed.

---

## [0.11.3] — 2026-06-28

### Added
- **`antcrew trace --prune N [--yes]`** — prune flag merged into the main `trace`
  command (replaces the now-removed `trace-db prune` sub-group). Usage:
  `antcrew trace ~/.antcrew/trace.db --prune 30 --yes`.
- **`antcrew trace --dump FORMAT [--dump-output FILE] [--dump-calls] [--dump-since N] [--dump-team T]`**
  — dump runs from a TraceLog as JSON or CSV directly from the `trace` command.
  Keeps the existing pipeline-artifact `antcrew export` command untouched.
- **Plugin system via `entry_points`** — third-party packages can now register
  agents by declaring an entry point in the ``antcrew.agents`` group:
  ```toml
  [project.entry-points."antcrew.agents"]
  my_agent = "my_package.agents:MyAgent"
  ```
  Plugins are merged into `AGENT_REGISTRY` at import time. Built-in names are
  never overwritten. Bad entry-point values are logged and skipped.
- **`BaseAgent.output_schema`** — set a Pydantic model class on any agent (via
  the constructor `output_schema=MySchema` or as a class attribute). Call
  `agent.system_structured(system, user)` to get a validated model instance back.
  Accepts an explicit `schema=` override and `max_retries=` (default 1).
- **`BaseAgent.max_cost_usd`** — per-agent cost cap. When set, `system()` raises
  `CostLimitExceeded` after the call if the accumulated cost for that agent
  (filtered by `agent.name` in `llm._usage_log`) exceeds the limit. Only counts
  this agent's own calls, so other agents in the same pipeline are unaffected.
- **`BaseAgent._check_agent_cost()`** — public helper to check the cap at any
  point (e.g. in custom `run()` implementations between multiple `system()` calls).

### Removed
- `antcrew trace-db prune` sub-group — superseded by `antcrew trace --prune`.

### Tests
- 28 tests in `test_features_v11.py` covering all of the above.
- Updated `TestTracePruneCLI` in `test_retry_prune_doctor.py` to use the new
  `antcrew trace --prune` flag instead of the removed `trace-db prune`.

---

## [0.11.2] — 2026-06-28

### Added
- **LLM retry wired into all adapters** — `BaseLLM._with_retry()` (exponential
  backoff + jitter, `Retry-After` header, up to 3 attempts by default) now wraps
  the actual network call in every adapter:
  - `AnthropicModel`: `_blocking_complete` and `_stream_complete` both retried.
  - `OpenAIModel`: blocking (`_with_retry` on `create`), streaming (whole stream
    wrapped in a helper), and reasoning paths all retried.
  - `GroqModel`: `_blocking_complete` wraps `create` with `_with_retry`.
  - `GeminiModel`: `_blocking_complete` wraps `httpx.post` with `_with_retry`.
  - 6 tests across all 4 adapters covering 429 / timeout / connection-error recovery.
- **`TraceLog.prune(days)`** — deletes runs (and their `agent_calls`) older than
  *N* days; raises `ValueError` for negative values. Returns deleted row count.
- **`antcrew trace-db prune DB DAYS [--yes]`** — CLI wrapper for `TraceLog.prune()`;
  prompts for confirmation unless `--yes` is supplied.
- **`antcrew doctor [--ping]`** — environment health check command:
  - Python version (≥3.11 required)
  - antcrew version
  - API key presence (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`,
    `GOOGLE_API_KEY`, `ANTCREW_API_KEY`) with masked key suffix
  - Optional dependency availability (fastapi, uvicorn, openai, chromadb, watchdog,
    aiosqlite, aiofiles)
  - `--ping`: makes a live API call per configured key to verify connectivity
  - Renders a Rich table with OK / WARN / FAIL status per check; summary line shows
    how many checks failed

### Tests
- 30 tests in `test_retry_prune_doctor.py` covering all of the above.

---

## [0.11.1] — 2026-06-28

### Security
- **Path traversal fix in `write_back()`** — `file_path` values from LLM-generated
  artifacts are now checked with `Path.is_relative_to()` after resolution. Any path
  that escapes `project_root` (e.g. `../../etc/passwd`, `../secret.env`) is silently
  skipped and a `SECURITY: skipped` message is printed. The fix is applied before any
  file I/O, including `dry_run` mode. Safe paths are unaffected.
- **API key authentication for `antcrew serve`** — the REST server now reads
  `ANTCREW_API_KEY` from the environment. When set, every request must carry
  `Authorization: Bearer <key>` or receive a `401 Unauthorized` with a
  `WWW-Authenticate: Bearer` header. When the env var is unset the server runs in
  open mode (backward-compatible for local dev). The `serve` CLI command gains a
  `--api-key` flag (also settable via `ANTCREW_API_KEY`) and prints a yellow warning
  when no key is configured and the host is not localhost.

### Added
- **18 security tests** in `test_security.py`:
  - `TestPathTraversal` (8): normal write, `../`, deep `../../`, leading-slash strip,
    skipped-entry metadata, mixed safe+unsafe batch, security message printed, dry-run.
  - `TestServerAuth` (7): open access when key unset, 401 without header, 401 wrong
    key, 200 correct key, `WWW-Authenticate` header present, POST protected, POST
    succeeds with auth.
  - `TestServeCLIAuth` (3): public-host warning, no warning when key set, auth-enabled
    status line printed.

---

## [0.11.0] — 2026-06-28

### Fixed
- **`coverage.json` untracked** — was accidentally committed in v0.10.9 before
  the gitignore entry took effect; removed via `git rm --cached`.

### Added
- **+20 tests** (`test_coverage_other_teams.py`, 1769 total):
  - **AGENT_REGISTRY** — 7 tests verifying `sprint_planner`/`doc_writer` are in
    the registry, `get_agent_class()` returns the right class, `instantiate_agent()`
    produces a working instance, and `antcrew agents --json` lists both names.
  - **`watch --repo-index`** — 3 tests: nonexistent path exits 1; non-fullstack team
    prints warning then exits (monkeypatched watchdog); file (not directory) exits 1.
  - **ContentTeam** — 5 tests: trace_log run, trace_log error path, max_cost_usd,
    memory propagated to agents, memory.store_run called on run.
    Coverage: **73 % → 98 %**.
  - **ResearchTeam** — 5 tests: same pattern as ContentTeam.
    Coverage: **72 % → 98 %**.
- Overall coverage: **83 % → 84 %** (1769 tests).

---

## [0.10.9] — 2026-06-28

### Fixed
- **`antcrew-watch-latest.json` tracked by git** — added to `.gitignore` and removed
  from the repository; file is generated at run-time and must not be committed.

### Added
- **`sprint_planner` and `doc_writer` in `AGENT_REGISTRY`** — both agents are now
  discoverable via `antcrew describe agents`, can be used in `custom` team YAML,
  and are returned by `get_agent_class()` / `instantiate_agent()`.
- **`antcrew watch --repo-index PATH`** — parity with `antcrew run`; builds a
  `RepoIndex` from the given directory and attaches it to `FullStackTeam` agents on
  every re-run. Validates path before watchdog starts; warns on non-fullstack team.
- **README: `antcrew sprint` section** — documents the new command with examples
  for plain arrays, object arrays, custom sprint size, `--json`, and `--output`.
- **+41 coverage tests** (`test_coverage_base.py` + `test_coverage_teams.py`):
  - `_fmt_time` (5 cases), `_sync_run` (3), `_ProgressPanel` (13) — `teams/base.py`
    goes from **21 % → 55 %**; the remaining uncovered lines are `run_with_approval()`
    which requires a live terminal + checkpointer.
  - `_apply_edit` (5 cases): valid dict artifact, valid list artifact, invalid JSON, unknown agent.
  - DevTeam + FullStackTeam: trace_log (run + error path), memory (spy on store_run),
    sandbox runner (called + exception swallowed), max_cost_usd, project_dirs, sprint_size.
  - `fullstack_team.py` goes from **80 % → 97 %**.
  - Overall coverage: **82 % → 83 %** (1749 tests).

---

## [0.10.8] — 2026-06-28

### Fixed
- **`describe --trace` bug** — `tl.stats()` was silently failing because the method
  was renamed to `get_stats()` in v0.10.0. The `except Exception: pass` guard hid the
  error. Now calls the correct method; historical cost shows in `describe` output.

### Added
- **`antcrew sprint`** — new standalone command: divides a ticket backlog (JSON array or
  file) into fixed-size sprints and renders them as Rich panels. Flags: `--size N`,
  `--json`, `--output FILE`. Accepts both plain string arrays and `{"title": ...}` objects;
  also understands `{"tickets": [...]}` run-state format from `antcrew run`.
- **`antcrew benchmark --context FILE`** — pre-computed scan context (from
  `antcrew scan --output`) is now passed to `FullStackTeam` for all fullstack benchmark
  cases, skipping the scanner LLM call and making benchmark runs faster and repeatable.
- **`antcrew watch --context FILE`** — pre-computed scan context is now accepted by
  `watch_cmd`; injected into `FullStackTeam` on every re-run. Warns if team is not
  `fullstack`; errors on missing file.
- **+26 tests** across `test_sprint_cmd.py` (15), `test_describe_trace.py` (4),
  `test_watch_benchmark_context.py` (7), closing all coverage gaps from v0.10.7.

---

## [0.10.7] — 2026-06-28

### Added
- **`antcrew cost`** — new command; shows aggregate LLM spend from a TraceLog DB:
  total runs, done/error counts, total cost, avg cost per run, token counts, and
  a per-team breakdown table. Flags: `--team`, `--since DAYS`, `--json`.
  Defaults to `~/.antcrew/trace.db` when no path is given.
- **`antcrew scan --since DAYS`** — filter the file tree to only files modified in
  the last N days. Useful for large repos: `antcrew scan ./src --since 7`
- **`antcrew describe --context FILE`** — shows a "Pre-loaded context" panel with
  the scan result that would be injected at run time (tech stack, what exists,
  what's missing). Works for single and multi-component contexts.
- **`antcrew run --repo-index PATH`** — build a `RepoIndex` from a directory and
  attach it to all agents in the fullstack team for semantic code search.
  Warns if used with a non-fullstack team; errors if the path doesn't exist.
- **Python 3.14** added to PyPI classifiers.
- **`.gitattributes`** — enforces LF line endings for all text files; eliminates
  per-commit CRLF conversion warnings on Windows.
- **README: brownfield subsections** — documented "scan once, run many times" with
  `--context`, `antcrew scan --since`, `--repo-index`, and `antcrew cost`.
- **+29 tests** across `test_cost_cmd.py` (11) and `test_new_flags.py` (18):
  `antcrew cost` (JSON, filtering, no-DB, by-team), `scan --since` (hides old files,
  shows new ones, key_files unaffected), `describe --context` (single + multi,
  missing file, shows tech stack / missing panel), `run --write-back` CLI,
  `run --repo-index` (bad path, wrong team, valid), `AsyncFullStackTeam.scan_context`
  (3 tests inc. async `run()`).

---

## [0.10.6] — 2026-06-28

### Added
- **`antcrew run --context` warnings** — consistent with `--project-dir` warnings:
  - Non-fullstack team with `--context`: yellow Warning (flag is ignored)
  - `--context` + `--project-dir` together: yellow Note that `--context` takes
    precedence and `--project-dir` will not trigger a new scan
- **`tests/test_describe_cmd.py`** — 19 new tests covering `antcrew describe`:
  - No-config mode: all four team presets, Consumes/Produces columns, Coherencia OK
  - `--team fullstack` shows `codebase_scanner` in the agent table
  - `--team fullstack` shows `project_dir` in the Consumes column
  - `--config YAML` with dev, custom, and fullstack configs
  - JSON config accepted
  - Missing config exits 1
- **`TestContextWarnings`** (3 tests in `test_describe_cmd.py`):
  - `--context` with dev team prints Warning
  - `--context` with fullstack team: no spurious warning
  - `--context` + `--project-dir`: shows precedence Note

---

## [0.10.5] — 2026-06-28

### Added
- **`antcrew run --context FILE`** — skip CodebaseScannerAgent by injecting a pre-computed
  scan result from `antcrew scan --output ctx.json`. Enables the two-step brownfield workflow:
  ```
  antcrew scan ./src --model claude --output ctx.json   # one-time scan
  antcrew run "Add billing" --team fullstack --context ctx.json  # no re-scan
  antcrew run "Add tests"   --team fullstack --context ctx.json  # reuse same context
  ```
- **`FullStackTeam(scan_context=…)`** — programmatic equivalent of `--context`; accepts
  the same JSON dict returned by `antcrew scan --json`
- **`CodebaseScannerAgent` short-circuit** — if `codebase_analysis` (or `codebase_analyses`)
  is already set in the state when the scanner runs, it passes through without an LLM call
- **+8 tests**: `TestScannerShortCircuit` (3) and `TestFullStackTeamScanContext` (5) covering
  short-circuit logic, single/multi-component injection, CLI `--context` flag, and
  missing-file exit-code check

---

## [0.10.4] — 2026-06-28

### Added
- **`antcrew scan --output FILE`** — save scan results to a JSON file instead of stdout;
  combines with `--model` to persist LLM analysis: `antcrew scan ./src --model claude --output scan.json`
- **`antcrew show --json` now outputs pure JSON** — the header was printed even in JSON mode,
  breaking `antcrew show run.json --json | jq '...'`; fixed to emit only the JSON object
- **`tests/test_inspect_cmds.py`** — 23 new tests covering `antcrew show` (inc. codebase scan
  display), `antcrew extract` (code/test/devops, --no-tests, --no-devops, --dry-run),
  and `antcrew agents --json`

### Fixed
- `antcrew show --json` printed the "AntCrew show" header before the JSON; now pure JSON output

---

## [0.10.2] — 2026-06-28

### Added
- **`antcrew scan --json`** — machine-readable JSON output; single dir → flat object,
  multiple dirs → `{components:[…]}`; with `--model` includes full `CodebaseAnalysis`
  fields. Useful for scripting: `antcrew scan ./src --model claude --json | jq '.tech_stack'`
- **`antcrew watch --project-dir`** — passes the directory to `FullStackTeam` so
  `CodebaseScannerAgent` gets brownfield context on every triggered re-run
- **`antcrew watch --write-back PATH`** — after each re-run automatically calls
  `write_back()` to apply generated artifacts to the target directory
- **`antcrew agents --json`** — outputs the agent registry as a JSON array for
  programmatic use: `antcrew agents --json | jq '.[].name'`
- **`antcrew show` displays `codebase_analysis`** — when a fullstack run was saved
  after a brownfield scan, `antcrew show` now renders the codebase context panel
  (tech stack, what exists, what's missing, continuation context) before the PRD

### Fixed
- `antcrew scan` Windows drive-letter bug: paths like `C:\Users\…` were incorrectly
  parsed as `label=C`, `path=\Users\…`. Fixed to check `colon > 1` (same as
  `_parse_project_dirs`)
- `antcrew run --project-dir` with non-fullstack teams now prints a yellow warning
  instead of silently ignoring the flag
- `antcrew watch --project-dir` with non-fullstack teams now also prints a warning

---

## [0.10.1] — 2026-06-28

### Added
- **`antcrew scan PATH [PATH…]`** — preview what `CodebaseScannerAgent` would see
  before running the full pipeline; shows file tree and detected key files without
  any LLM call; `--model` runs the full LLM analysis; `--no-tree` skips the tree.
  Supports `label:path` syntax for named components:
  `antcrew scan backend:./src frontend:./client`
- **Multi-directory `--project-dir`** on `antcrew run` — repeat the flag for each
  component: `antcrew run "..." --team fullstack --project-dir backend:./src --project-dir frontend:./client`
  Single entry → `project_dir`; multiple → `project_dirs` (used by `CodebaseScannerAgent`)

### Fixed
- `_parse_project_dirs` now correctly handles Windows absolute paths (`C:\…`)
  by treating single-character prefixes before `:` as drive letters, not labels

---

## [0.10.0] — 2026-06-28

### Added
- **Brownfield write-back** (`antcrew/core/writeback.py`) — apply generated
  artifacts to their real paths on disk instead of always writing to `./generated/`:
  - `write_back(state, project_root, *, dry_run, yes, confirm_fn, print_fn)`
  - Collects `code_artifacts`, `test_artifacts`, `devops_artifacts`, and
    `documentation_artifacts` from any state shape (RunResult, raw dict, `.state`)
  - Infers `create` vs `modify` from whether the target file already exists
  - Unchanged files (empty unified diff) are silently skipped without a prompt
  - `confirm_fn` hook for per-file interactive confirmation
- **`antcrew write-back`** CLI command — apply a saved state to the filesystem:
  - `antcrew write-back run.json --project-root ~/myproject --dry-run`
  - `antcrew write-back run.json --project-root ~/myproject --yes`
  - Auto-detects `project_root` from `project_dir` / `project_dirs` in the state
    (set by `CodebaseScannerAgent`) when `--project-root` is omitted
- **`antcrew run --write-back PATH`** — write artifacts immediately after a run;
  `--write-back-yes` skips confirmation
- **`antcrew describe --trace PATH`** — shows historical average cost and total cost
  from a TraceLog DB below the coherence check; auto-reads `~/.antcrew/trace.db`
- **`CONTRIBUTING.md`** — setup guide, code layout map, how to add agents and CLI
  commands, good-first-issues list

### Fixed
- `pyproject.toml` version was stuck at `0.8.1`; now synced with `__version__ = "0.9.9"`
- `asyncio.TaskGroup` (used in `DevTeam`) confirms Python ≥ 3.11 is a real requirement

### Improved
- **README** — added sections for `CustomTeam`, `TemplateAgent`, `AzureOpenAIModel`,
  Agent Presets, Agent Tools, Brownfield write-back, and 12 previously undocumented
  CLI commands; updated test count
- Test suite grows to **1 551 tests** (18 new for write-back)

---

## [0.9.9] — 2026-06-28

### Refactored
- **`antcrew/cli/` package — full split** — `antcrew/cli.py` (4 188 lines)
  fully decomposed into 18 focused modules; `__init__.py` is now 35 lines:
  - `_app.py` — Typer singletons (`app`, `_flow_app`, `_project_app`,
    `console`, `_TEAM_CHOICES`, `_MODEL_HELP`)
  - `_shared.py` — shared display helpers (`_build_team`, `_print_state`,
    `_print_test_results`, `_print_usage`, `_print_state_raw`)
  - `_run_helpers.py` — streaming/REPL helpers (`_print_dry_run`,
    `_run_with_stream`, `_save_outputs_to_dir`, `_run_repl`)
  - `_templates.py` — `_YAML_*` / `_MAIN_*` init scaffold strings
  - `run_cmd.py` — `run` command
  - `init_cmd.py` — `init` command
  - `setup_cmds.py` — `setup`, `cache-clear`, `cache-stats`, `serve`
  - `eval_cmds.py` — `interactive`, `eval`
  - `inspect_cmds.py` — `show`, `extract`, `describe`, `agents`
  - `project_cmds.py` — `project run/show/history`
  - `flow_cmds.py` — `flow show/validate`
  - `trace_cmd.py` — `trace`, `replay`
  - `publish_cmd.py` — `publish`
  - `ops_cmds.py` — `benchmark`, `watch`, `export`, `diff`, `test`
  - `graph_cmd.py` — `graph`, `lint`
  - `history_cmd.py` — `history`
  - `validate_cmd.py` — `validate`
- **`antcrew/agents/registry.py`** — central agent registry replacing the
  duplicate dicts in `config.py` and `cli.py`.  `AGENT_REGISTRY`,
  `get_agent_class()`, and `instantiate_agent()` are the single source of
  truth for built-in agent types.
- **`antcrew/config._resolve_agent`** updated to delegate to
  `antcrew.agents.registry.instantiate_agent()`.
- **`antcrew agents` CLI command** updated to discover agents via
  `AGENT_REGISTRY` instead of a hard-coded local dict.

---

## [0.9.8] — 2026-06-28

### Fixed
- **`team_file:` path now resolves relative to the config file** instead of
  CWD. A `team_file: teams/inner.yaml` in `/project/outer.yaml` now loads
  `/project/teams/inner.yaml` regardless of where `antcrew run` is invoked.
  Nested teams recursively resolve their own `team_file:` paths relative to
  their own directory.

### Added
- **`antcrew validate` covers v0.9.5 features**:
  - Validates `on_error:` value is `raise` or `skip`; exits 1 on invalid values.
  - Validates `timeout:` is a positive number; exits 1 on non-numeric or zero.
  - Shows `skip[=default]` and `timeout:Ns` in the flags column.
  - Handles `team_file:` steps: checks file exists (warning if missing),
    extracts nested team output keys into the dataflow graph so subsequent
    steps that use those keys don't trigger false-positive warnings.
  - `validate` instantiation check now passes `base_dir` so `team_file:` and
    `system_prompt_file:` paths resolve correctly.

- **`--dry-run` shows `timeout`, `on_error`, and `default` flags** for each
  step. `_NestedTeamAgent` steps display `name/* (merged)` in the
  `output_key` column instead of the internal placeholder.

- **`--repl-stateful`** — REPL mode that carries the output state from each
  iteration into the next. The previous run's `output_key` values are
  available as `{placeholder}` interpolations in the next request's prompts.
  Type `quit` or Ctrl-C to stop.

- 15 new tests (1533 total, across `test_cli_validate.py` and
  `test_cli_run_ergonomics.py`).

---

## [0.9.7] — 2026-06-28

### Added
- **`register_transform(name, fn)`** — register a custom `post_process`
  transform at runtime. Once registered, the name is available in
  `post_process:` config fields everywhere.

  ```python
  from antcrew import register_transform

  def slugify(text: str) -> str:
      return text.lower().replace(" ", "-")

  register_transform("slugify", slugify)
  ```

  Also exported from `antcrew` top-level namespace.

- **`antcrew agents`** CLI command — lists all built-in agent types
  (name, class, role description) plus all registered `post_process`
  transforms. Useful for discovering what's available without opening docs.

---

## [0.9.6] — 2026-06-28

### Added
- **Optional `request` argument in `antcrew run`** — omitting the positional
  argument now prompts interactively (`> Request:`) instead of showing a usage
  error. Useful in terminal workflows and CI pipelines with stdin.

- **`--request-file` / `-r`** — read the request from a file:
  `antcrew run --config team.yaml --request-file task.md`. The file content is
  stripped before use. Error if the file doesn't exist.

- **`--output-dir` / `-O`** — save every step's `output_key` value to a
  separate file in the specified directory (created if absent). String outputs
  go to `<key>.txt`; dict outputs go to `<key>.json`. The `request` key is
  never written.

- **`--repl`** — interactive loop mode. Runs the pipeline repeatedly, reading
  a new request each iteration. Type `quit`/`exit`/`q` or press Ctrl-C to
  exit. Works with `--output-dir` (saves outputs after each iteration).

- 16 new tests in `tests/test_cli_run_ergonomics.py` (covers all four features).

---

## [0.9.5] — 2026-06-28

### Added
- **`timeout: N` per step** — kills a step (via thread timeout) if it takes
  longer than `N` seconds. Raises `TimeoutError` on expiry; works with retries
  and `on_error`.

  ```yaml
  steps:
    - name: slow_step
      system_prompt: "Process this."
      output_key: result
      timeout: 30        # seconds
  ```

- **`on_error: skip | raise` per step** — controls what happens when all
  retries are exhausted. Default `raise` preserves existing behaviour.
  `skip` writes `default:` into the output key and lets the pipeline continue.

  ```yaml
  steps:
    - name: optional_enricher
      system_prompt: "Enrich the data if you can."
      output_key: enriched
      on_error: skip
      default: "N/A"
  ```

- **`default:` per step** — fallback value written to the step's `output_key`
  when `on_error: skip` is triggered. Accepts any YAML scalar (string, int,
  bool, null). Ignored when `on_error: raise`.

- **`team_file:` step type** — embed a full CustomTeam YAML as a step inside
  another pipeline. The nested team's output keys are merged directly into the
  parent state.

  ```yaml
  # outer_team.yaml
  team: custom
  steps:
    - name: research
      team_file: research_team.yaml   # loads & runs as sub-pipeline
      input_key: topic               # passes state["topic"] as the nested request
    - name: writer
      system_prompt: "Write based on: {summary}"
      output_key: article
  ```

  - Nested team shares the parent's LLM instance.
  - Nested team can declare its own `vars:`.
  - Paths are resolved relative to the current working directory.

- 15 new tests for the above features (112 total in `test_custom_team.py`).

---

## [0.9.4] — 2026-06-28

### Added
- **`vars:` in CustomTeam YAML** — team-level state defaults injected before
  step 1 runs, enabling reusable configuration without hardcoding values in
  prompts:

  ```yaml
  team: custom
  vars:
    language: Python
    tone: concise
  steps:
    - name: planner
      system_prompt: "Create a {language} plan in {tone} style."
      output_key: plan
  ```

  - `vars` keys are available to `{key}` interpolation, `condition:`, and
    `user_template` in every step.
  - `request` always takes precedence over any `vars` key with the same name.
  - `antcrew validate` pre-populates the dataflow graph with `vars` keys so
    steps that reference them are not flagged as unknown-key warnings.
  - `CustomTeam(steps, llm, vars={...})` — also available programmatically.

- **`antcrew run --dry-run`** — inspect a CustomTeam pipeline without calling
  the LLM:

  ```
  Dry run — 3 step group(s), 4 agents
    [1/3]  planner        seq    plan
    [2/3]  backend        par    backend_code
           frontend       par    frontend_code    if:plan
    [3/3]  reviewer       seq    review           retry×2
  No LLM calls will be made.
  ```

  - Shows step type (seq/par), output_key, condition, and retry flags.
  - Exits 0 without touching the LLM or TraceLog.
  - 13 new tests added to `tests/test_custom_team.py` (82 total).

---

## [0.9.3] — 2026-06-28

### Added
- **`post_process`** — declarative output transforms applied before storing to
  state (and before `save_output`).  Accepts a single name or a list:

  ```yaml
  - name: code_gen
    system_prompt: "Generate Python code."
    output_key: code
    post_process: strip_fences            # single transform
  
  - name: summariser
    system_prompt: "Summarise in one line."
    output_key: summary
    post_process: [strip_fences, strip]   # chained transforms
  ```

  Built-in transforms:

  | Name            | Effect                                          |
  |-----------------|------------------------------------------------|
  | `strip`         | `str.strip()`                                  |
  | `lower`         | `str.lower()`                                  |
  | `upper`         | `str.upper()`                                  |
  | `first_line`    | First non-blank line                           |
  | `last_line`     | Last non-blank line                            |
  | `strip_fences`  | Remove outer ` ``` lang … ``` ` code fence     |

  - Transforms are applied to string outputs only; `output_json: true` results
    (dict) pass through unchanged.
  - `save_output` persists the post-processed value.
  - `antcrew validate` validates transform names at static analysis time and
    shows them in the flags column as `pp:(name,…)`.
  - `POST_PROCESS_TRANSFORMS` dict exported from `antcrew.agents.template_agent`
    for programmatic registration of custom transforms.
  - 19 new tests added to `tests/test_template_agent.py` (106 total).

---

## [0.9.2] — 2026-06-28

### Added
- **`user_template`** — format string for the user message with `{key}`
  interpolation, enabling clean multi-input steps without polluting the
  system prompt with data:

  ```yaml
  - name: reviewer
    system_prompt: "You are a senior code reviewer."
    user_template: |
      Plan:
      {plan}

      Backend code:
      {backend}

      Frontend code:
      {frontend}

      Identify issues across all three artifacts.
    output_key: review
  ```

  - Mutually exclusive with `input_key` (both → `ValueError`).
  - Always interpolated using `_interpolate()` (the `interpolate: false` flag
    only suppresses system prompt interpolation).
  - Unknown `{key}` placeholders are left as-is, consistent with system prompt
    interpolation behaviour.
  - `antcrew validate` checks `user_template` placeholder keys against the
    dataflow graph (warns on unknown keys, errors on mutual-exclusion conflict),
    and shows `user_tmpl` in the flags column.
  - 12 new tests added to `tests/test_template_agent.py` (87 total).

---

## [0.9.1] — 2026-06-28

### Added
- **`system_prompt_file`** — alternative to `system_prompt` for long prompts:

  ```yaml
  steps:
    - name: planner
      system_prompt_file: prompts/planner.md   # loaded at agent init time
      output_key: plan
  ```

  - Mutually exclusive with `system_prompt` (both → `ValueError`).
  - Relative paths are resolved relative to the YAML config file's parent
    directory when loading from a file; relative to CWD when loading from a
    dict/string.
  - Full interpolation (`{key}`) and all other TemplateAgent features work
    on the loaded content.
  - `antcrew validate` accepts `system_prompt_file` and warns (not errors) if
    the referenced file does not exist at validation time.

- **`save_output`** — automatically persist a step's output to a file:

  ```yaml
  steps:
    - name: planner
      system_prompt: "Plan the task."
      output_key: plan
      save_output: artifacts/plan.md   # created after the step completes
  ```

  - Parent directories are created automatically (`mkdir -p`).
  - String outputs are written as-is; dict outputs (`output_json: true`)
    are serialized as pretty-printed JSON.
  - Resolved relative to the working directory at run time.
  - 16 new tests added to `tests/test_template_agent.py` (75 total).

---

## [0.9.0] — 2026-06-28

### Added
- **Step-level progress for `CustomTeam`** — when running via `antcrew run
  --config team.yaml`, each step now prints a timestamped progress line as it
  starts, completes, or is skipped, instead of a generic "Running…" spinner:

  ```
  [1/3] planner...
  [1/3] ✓ planner (2.3s)
  [2/3] backend + frontend (parallel)...
  [2/3] ✓ backend + frontend (parallel) (5.1s)
  [3/3] reviewer...
  [3/3] ✓ reviewer (1.8s)
  ```

  - `CustomTeam._on_step` attribute — optional callback called as
    `_on_step(name, event)` where `event` is `"start"` | `"done"` | `"skip"`.
    The CLI sets this before calling `team.run()` and clears it afterward.
  - Callback exceptions are silently swallowed so a buggy progress handler can
    never abort the pipeline.
  - Parallel groups emit a single event with a combined name (`"be + fe"`).
  - Works in both `--stream` and `--no-stream` modes.
  - 9 new tests added to `tests/test_custom_team.py` (69 total).

---

## [0.8.9] — 2026-06-28

### Added
- **`antcrew validate <team.yaml>`** — validates a team config without running it
  or making any API calls.
  - Parses YAML/JSON and reports syntax errors.
  - For `team: custom` configs: shows a step table (name, type, output_key,
    flags), checks required fields (`name`, `system_prompt`), and attempts
    agent instantiation with `SimulatedLLM`.
  - **Static dataflow analysis**: warns when a `{key}` interpolation placeholder
    or a `condition:` key references a state key not yet produced by an earlier
    step (e.g. typos in output_key chains).
  - `--strict` flag promotes warnings to errors (exit 1).
  - Accepts YAML or JSON; works with sequential, parallel, and mixed pipelines.
  - 24 tests in `tests/test_cli_validate.py`.

---

## [0.8.8] — 2026-06-27

### Added
- **JSON output mode in `TemplateAgent`** — two new config keys:
  - `output_json: true` — tells the step to call `system_parsed()` instead of
    `system()`, so the LLM response is parsed as JSON and the output key stores
    a Python `dict` rather than a raw string.  Downstream steps and interpolation
    (`{plan}` → `str(dict)`) both receive the structured value.
  - `output_parse_retries: N` — on JSON parse failure the agent retries up to *N*
    extra times, each time sending the previous invalid response back to the model
    with a correction hint (delegates to `BaseAgent.system_parsed`).
  - Both keys pass through `CustomTeam` YAML pipelines unchanged; they are
    TemplateAgent-level config, not stripped by `_TEAM_KEYS`.
  - 9 new tests added to `tests/test_template_agent.py` (59 total).

---

## [0.8.7] — 2026-06-27

### Added
- **System prompt interpolation in `TemplateAgent`** — ``{key}`` placeholders
  in the ``system_prompt`` are replaced with ``str(state[key])`` at runtime,
  enabling multi-value injection without chaining ``input_key``.
  - Unknown keys (not in state) and ``None`` values are left as ``{key}``.
  - Only ``{identifier}`` patterns are matched — JSON syntax
    (``{"key": "value"}``), positional (``{0}``), and format-spec
    (``{:.2f}``) are untouched.
  - Opt out per-agent with ``interpolate: false`` in the config (default:
    ``true``).
  - Works transparently through `CustomTeam` pipelines: a producer's
    ``output_key`` can be injected directly into the next step's
    ``system_prompt`` via ``{output_key_name}``.
  - ``_interpolate(template, state)`` helper function is exported from
    ``antcrew.agents.template_agent`` for programmatic use.
  - 18 new tests added to `tests/test_template_agent.py` (50 total).

---

## [0.8.6] — 2026-06-27

### Added
- **Conditional steps in `CustomTeam`** — each step config accepts an optional
  ``condition`` key that guards execution:
  - A single key name (str): the step runs only if ``state[key]`` is truthy.
  - A list of key names: the step runs only if **all** keys are truthy.
  - Missing or falsy condition → step is silently skipped; its ``output_key``
    is not written to state.
  - Works for sequential steps and steps inside ``parallel:`` groups (only the
    passing members of a parallel group are submitted to the thread pool).
  - ``condition`` is stripped before the config is forwarded to `TemplateAgent`.
  - Internal ``_condition_met()`` helper and ``_parse_condition()`` normaliser
    added (both tested independently).
  - 15 new tests added to `tests/test_custom_team.py` (60 total).

---

## [0.8.5] — 2026-06-27

### Added
- **Per-step retry policy in `CustomTeam`** — each step config accepts two
  optional keys:
  - ``max_retries`` (int, default 0) — number of additional attempts after the
    first failure.
  - ``retry_delay`` (float seconds, default 0) — sleep between attempts; skipped
    when 0 to keep test suites fast.
  - Works for both sequential steps and steps inside ``parallel:`` groups.
  - Retry keys are stripped before the config is forwarded to `TemplateAgent`
    so they don't pollute the agent's config.
  - Internal `_Step` dataclass wraps each `TemplateAgent` with its retry policy;
    `_agents` (flat list) remains unchanged for backward compatibility.
  - 11 new tests added to `tests/test_custom_team.py` (45 total).

---

## [0.8.4] — 2026-06-27

### Added
- **Parallel steps in `CustomTeam`** — group agents under a ``parallel:`` key
  to run them concurrently in a thread pool, then merge their outputs before
  the next sequential step begins.
  - Each parallel agent receives a snapshot of the current state so reads
    don't race; writes are merged atomically after all agents in the group
    finish.
  - Configurable thread-pool size via the ``max_workers`` constructor argument
    (default: 4).
  - Supported in both Python (`{"parallel": [cfg, …]}` dict) and YAML
    (`parallel:` list under `steps:`).
  - `_step_groups` attribute on `CustomTeam` exposes the parsed structure;
    `_agents` remains a flat list of all agents for backward compatibility.
  - 10 new tests added to `tests/test_custom_team.py` (34 total).

---

## [0.8.3] — 2026-06-27

### Added
- **`CustomTeam`** — a sequential multi-agent pipeline defined entirely in
  YAML/dicts, no Python required.
  - Declare an ordered `steps:` list of TemplateAgent configs; each step's
    `output_key` is automatically available to subsequent steps via `input_key`.
  - Supports `max_cost_usd`, `trace_log`, and
    :class:`~antcrew.core.pipeline.Pipeline` carry-over (`_initial_state`).
  - `team: custom` in `agentteam.yaml` activates it via `config.py`; the
    `steps:` list is parsed and each entry is built as a TemplateAgent.
  - `antcrew init --template custom` generates a ready-to-run three-step
    (planner → executor → reviewer) YAML + `main.py`.
  - `antcrew run --config team.yaml` output panel shows all step outputs
    automatically (generic key → value rendering).
  - Exported from the top-level `antcrew` namespace as `CustomTeam`.
  - 24 tests in `tests/test_custom_team.py`.

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
