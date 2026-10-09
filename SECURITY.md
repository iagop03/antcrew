# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities to **security@antcrew.org**. Do not open public GitHub
issues for security bugs.

We aim to respond within **72 hours** and will coordinate a fix and disclosure timeline with you.

## Scope

This policy covers the `antcrew` Python SDK, including the bundled `antcrew_engine` package
and the `platform/` product API.

---

## Threat Model

### Assets

| Asset | Sensitivity | Where stored |
|---|---|---|
| LLM API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, …) | Critical | Caller's environment / OS env vars — never in TraceLog |
| Agent prompts and responses | High | TraceLog SQLite (`~/.antcrew/trace.db`) — filesystem-protected |
| HITL decision audit trail | High | TraceLog `hitl_decisions` + `execution_events` tables |
| EvidencePackage exports | Medium | Caller's filesystem; `document_hash` covers tamper detection |
| Platform API keys (`acw_live_…`) | Critical | antcrew-platform DB (bcrypt-hashed, prefix-indexed) |
| Run state (ticket data, code artifacts) | High | antcrew-platform DB (EncryptedJSON column) |

### Actors and trust levels

| Actor | Trust level | Notes |
|---|---|---|
| Pipeline code (your scripts) | Fully trusted | Has direct Python access to all SDK objects |
| LLM output | Untrusted | Validated through typed contracts; never `eval`'d or `shell=True` |
| HITL reviewer | Semi-trusted | Can only approve/reject; cannot modify code directly |
| Platform API caller with `admin` role | Trusted | Can read/write all workspaces |
| Platform API caller with workspace-scoped key | Limited trust | Isolated to their `workspace_id`; `_assert_run_access` enforces boundary |
| TraceLog database file | Trusted read, untampered write | SHA-256 chain detects post-write modification |

### Trust boundaries

```
  Caller env
  ┌──────────────────────────────────────────┐
  │  Pipeline code  ─── API keys (env vars)  │
  │       │                                  │
  │   EngineLoop                             │
  │       │                                  │
  │   HitlReviewer ──── request_review() ───►│──► HITL channel (untrusted callback)
  │       │                                  │         │
  │   TraceLog (SQLite)◄────────────────────►│◄── reviewer decision (semi-trusted)
  └──────────────────────────────────────────┘
           │
           ▼
  Platform API (FastAPI)
  ┌──────────────────────────────────────────┐
  │  /runs/*   ─── _assert_run_access()      │
  │  /engine/run ── workspace isolation      │
  └──────────────────────────────────────────┘
           │
           ▼
  LLM providers (untrusted output)
```

### Attack scenarios and mitigations

| Scenario | Attack | Mitigation |
|---|---|---|
| Replay stale approval | Attacker re-sends an old approval for new content | `_content_hash` binding in HitlReviewer; mismatched hash → reject |
| Duplicate webhook delivery | HITL webhook fires twice, producing double-approval | `_review_id` deduplication; second delivery silently dropped |
| Unauthorized reviewer approves | Reviewer not in team's allowlist sends approval | `allowed_reviewers` whitelist; non-listed reviewer_id → reject |
| Tampered TraceLog | Attacker edits hitl_decisions or execution_events | SHA-256 hash chain; any field change breaks chain, detected by `antcrew verify` |
| Prompt injection in LLM output | LLM output contains shell command to be `exec`'d | `shell=False` enforced; LLM output never executed directly |
| Path traversal in tool input | LLM output contains `../../../etc/passwd` as file path | `_safe_path()` validates with `Path.is_relative_to(root)` before any write |
| Cross-workspace data leak | API key from workspace A reads workspace B's runs | `_assert_run_access()` raises 403 on cross-workspace access |
| Credential leak into TraceLog | API key appears in a prompt_snippet | `_redact_secrets()` strips known patterns from snippets before storage |
| Auto-approve without audit trail | `auto_approve=True` leaves no reviewer record | `reviewer_id="system:auto_approve"` written to TraceLog |

---

## Verified Security Guarantees

These behaviors are enforced in code and covered by automated tests:

| Guarantee | Mechanism | Test coverage |
|---|---|---|
| HITL callback error → REJECT, never APPROVE | `FlexibleHITL.gate()` wraps callback in try/except; default is REJECT | `TestGovernanceGuarantees::test_callback_error_never_produces_approval` |
| Stale content hash → reject | `HitlReviewer._run()` compares echoed `_content_hash` to computed hash | `TestSecurityMatrix::test_stale_content_hash_rejected` |
| Duplicate review_id → drop | `HitlReviewer._processed_review_ids` set | `TestSecurityMatrix::test_duplicate_review_id_rejected` |
| Unauthorized reviewer → reject | `HitlReviewer` checks `allowed_reviewers` | `TestUnauthorizedReviewer` (5 tests) |
| TraceLog failure → decision still enforced | try/except around `record_hitl()` call | `TestPersistenceFailure` (2 tests) |
| Chain tamper → verify detects it | SHA-256 hash chain in `execution_events` | `TestChainIntegrity` (3 tests) |
| Budget exceeded → EngineLoopError | `EngineLoop._max_cost_usd` gate post-dispatch | `test_budget_exceeded_raises` |
| Cross-workspace access → 403 | `_assert_run_access()` / `ws_accessible()` | `test_platform_workspace_isolation.py` (7 tests) |

## Limitations (not currently enforced)

| Limitation | Notes |
|---|---|
| TraceLog filesystem permissions | SQLite file should be `chmod 600` — not enforced by the SDK itself |
| Prompt injection from LLM | Semantically subtle injections not caught; structural injection (path traversal, shell=True) is prevented |
| HITL channel authenticity | SDK validates decision content but not channel identity; channel must be secured at the infrastructure level |
| `full_trace=True` stores raw prompts | When enabled, complete prompts are stored unredacted (snippet redaction only applies to snippets) |

---

## Cryptographic Choices

### API keys passed to LLMs

API keys (ANTHROPIC_API_KEY, etc.) are read from environment variables and passed directly to
provider SDK clients. They are never logged, stored, or included in TraceLog entries. If you
use `FileLLMCache`, the cache key is a hash of the request content — no credentials are stored.

### TraceLog

TraceLog writes agent events to a SQLite file. It records prompt and response **snippets**
(first 300 characters), which are passed through `_redact_secrets()` to strip common credential
patterns before storage. `full_trace=True` stores complete prompts without redaction — only use
it in trusted environments.

The `hitl_decisions` and `execution_events` tables use a SHA-256 hash chain. Any post-write
modification to any field, row insertion, or deletion is detectable via `antcrew verify`.

---

## PR Checklist

Before merging a PR that touches any of the following surfaces, verify each item.

### 1. New filesystem write from untrusted input

> Pattern: `_safe_path(rel)` using `Path.is_relative_to()`, never `str.startswith()`.

- [ ] Is the path validated with `is_relative_to(root)` before any write?
- [ ] Is an absolute path from untrusted input explicitly rejected?
- [ ] Does the check run before both `mkdir` and `write_text`?

### 2. New code execution surface (tool or agent capability)

> Pattern: validate inputs; never pass untrusted strings to `shell=True`.

- [ ] Is `shell=False` (the default) used in all `subprocess.run()` / `Popen` calls?
- [ ] Is the subprocess environment stripped to a safe allowlist — no API keys, no DB URLs?
- [ ] If the tool accepts a file path from the LLM output, is it checked with `_safe_path()`?

### 3. New LLM model or provider

- [ ] Does `build_llm()` pass `api_key` only from trusted sources (env vars or explicit caller)?
- [ ] Is `extra_body` validated to contain only non-sensitive forwarded metadata?

### 4. New constant-time comparison

> Pattern: `hmac.compare_digest(a, b)` — never `==` on secret values.

- [ ] Are all comparisons involving tokens or keys using `hmac.compare_digest()`?

### 5. New HITL integration

- [ ] Does a callback exception default to REJECT (not APPROVE)?
- [ ] Is `reviewer_id` populated in all decision paths?
- [ ] Is the decision recorded in TraceLog before any downstream action?
