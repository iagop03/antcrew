# Architecture decisions

This page records the significant architectural decisions made during antcrew's development.
It replaces the v2 architecture document (June 2026), which described a single-repo future
that has since diverged from the actual implementation.

Entries are ordered newest first. Each includes what was decided, what the original plan was,
and why the actual choice was made.

---

## 2026-08 — HITL: multiple channels, one canonical type

**Decision:** HITL review uses three delivery channels (CLI interactive, engine HitlReviewer callback, platform queue with Slack/webhooks). Each channel is independent code. They share one canonical data type: `HitlDecision` from `antcrew_engine`, which all channels must produce to unblock the engine.

**Original plan:** a unified `ApprovalGate` primitive that all channels would instantiate.

**Why it changed:** the channels have different blocking models (synchronous CLI, threading.Event for engine, async HTTP for platform). A shared base class would have forced the lowest-common-denominator API across three very different call sites. `HitlDecision` as a TypedDict is a lighter shared contract: it defines the boundary without prescribing the channel's internal implementation.

**Residual risk:** adding a fourth channel (e.g. email, Teams) requires implementing the full channel independently. The contract (`HitlDecision`) keeps them consistent, but there is no base class enforcing it.

---

## 2026-08 — Security analysis: two layers, not one

**Decision:** security analysis is split by execution context:

- **Layer 1 (antcrew teams)** → `SecurityAgent` — operates on `TeamState` artifacts, produces `SecurityReport`, runs within a `Supervisor` or `DevTeam` pipeline.
- **Layer 2 (antcrew-engine loops)** → `SecurityAuditor` (LLM cross-file) + `SecurityScanner` (pip-audit CVEs) — operate on the full source tree as `Capability` instances inside an `EngineLoop`.

`SecurityAuditor` and `SecurityAgent` both do LLM-based code analysis. They are not consolidated because they run in different execution contexts with different inputs (TeamState vs. filesystem). `SecurityScanner` (pip-audit) has no overlap with either.

**Guideline:** if you are writing a team pipeline, use `SecurityAgent`. If you are writing an engine loop, use `SecurityAuditor` and/or `SecurityScanner`.

---

## 2026-07 — antcrew-engine as a second layer, not a rename

**Decision:** the "operator/artefact" paradigm lives in `antcrew-engine` as a separate package, not as a rename of `antcrew/agents/`. `antcrew/agents/` and `antcrew/teams/` retain their vocabulary. `antcrew/engine/` is a shim that re-exports from `antcrew_engine` for backward compatibility.

**Original plan (v2 architecture doc, June 2026, decision 5.1):** rename `agents/` → `operators/` in-place. Estimated 6–8 weeks of interface-preserving migration.

**Why it changed:** renaming the existing tree would have broken integrations during the transition and required coordinated migration of all downstream code. The actual need was a new execution model for goal-directed loops — distinct enough from the existing team pipeline model that sharing the same directory would have muddied both. Building `antcrew-engine` as a clean new layer meant the existing team framework (LangGraph-based, well-tested, ~2600 tests) stayed stable while the new paradigm was developed in isolation.

---

## 2026-07 — LangGraph in Layer 1, custom loop in Layer 2

**Decision:** Layer 1 (antcrew) uses LangGraph for structured team pipelines. Layer 2 (antcrew-engine) uses a custom `EngineLoop` with no LangGraph dependency.

**Original plan (v2 doc, decision 5.2):** LangGraph throughout, no problems identified.

**Why it changed:** LangGraph's static graph model works well for pipelines where the flow of agents is declared upfront (PM → developer → QA). It does not fit goal-directed loops where the next capability depends on whether the current artifact satisfies typed contracts — that requires dynamic branching based on `ArtifactStore` state, not a static graph. `EngineLoop` was built to handle that model. LangGraph was not abandoned; it continues to be the right tool for Layer 1.

---

## 2026-07 — ContextCompressor replaces HeadroomLLM

**Decision:** context compression uses `antcrew_engine.ContextCompressor`, built in-house: AST-aware compression for Python source files, line-based compression for text. No external library dependency.

**Original plan (v2 doc, section 5.4):** integrate [HeadroomLLM](https://github.com/thoughtbot/headroom) for context budget management.

**Why it changed:** HeadroomLLM was not yet at the maturity needed for production use at the time the feature was prioritised. Building `ContextCompressor` directly gave full control over the AST compression strategy (Python-specific, preserves function signatures and docstrings) and avoided a third-party runtime dependency on a library with unclear maintenance status.

---

## 2026-06 — Single-repo → four-repo split

**Decision:** the ecosystem is four separate repos (`antcrew`, `antcrew-engine`, `antcrew-platform`, `keybridge`) with independent version numbers and release cadences.

**Original plan:** single `antcrew` package with a sequential v0.1→v0.2→v0.3→v0.4→v1.0 roadmap.

**Why it changed:** the SaaS multi-tenant platform and the LLM proxy have different deployment models, dependencies, and audiences from the Python SDK. Combining them would have created a package that is simultaneously a client library and a FastAPI server — difficult to install, difficult to version, and impossible to make public (the platform contains billing logic). The four-repo model lets each component evolve and publish independently.

**Note on the v2 roadmap:** the version-gated feature bundles (v0.2 = HITL advanced, v0.3 = observability, v0.4 = plugin system) are obsolete. All of that content shipped as continuous semver releases across the four repos. The roadmap section of the June 2026 document should be treated as historical context, not a forward plan.
