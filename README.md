# antcrew

[![CI](https://github.com/iagop03/antcrew/actions/workflows/ci.yml/badge.svg)](https://github.com/iagop03/antcrew/actions)
[![PyPI](https://img.shields.io/pypi/v/antcrew)](https://pypi.org/project/antcrew/)
[![Python](https://img.shields.io/pypi/pyversions/antcrew)](https://pypi.org/project/antcrew/)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL%201.1-yellow.svg)](LICENSE)

**AI agents do the work. AntCrew governs it.**

Every governed execution produces a human-approved, verifiable record of what happened and why.

```bash
pip install antcrew
antcrew issue owner/repo#143
```

```
◆ DISCOVERY       Analyzing repository structure and affected modules
◆ PLAN            12 files · 3 new · 9 modified — estimated complexity: Medium
  ┌──────────────────────────────────────────────────────┐
  │  HUMAN REVIEW                                        │
  │  Approve plan before implementation starts?          │
  │  [A]pprove  [R]equest changes  [X] Reject            │
  └──────────────────────────────────────────────────────┘
◆ IMPLEMENT       BackendDev generating 9 files
◆ TEST            142 tests · 141 passed · 1 failed → auto-retry
◆ REVIEW          Reviewer: 2 findings fixed by developer
  ┌──────────────────────────────────────────────────────┐
  │  HUMAN REVIEW                                        │
  │  PR ready. Approve to push?                          │
  │  [A]pprove  [R]equest changes  [X] Reject            │
  └──────────────────────────────────────────────────────┘
◆ PR              github.com/owner/repo/pull/221 opened
  Cost: $1.84 · Time: 9m 12s · Human approvals: 2
```

---

## COBOL incident analysis

Banks and insurers run millions of lines of COBOL in production. When an incident hits — a wrong date calculation, a SQLCODE -803, a silent truncation — the usual process is: hunt for the affected program, manually trace which callers depend on it, escalate to a specialist who may or may not be available.

`antcrew cobol-incident` does that analysis in two minutes with a human-approved, auditable trail:

```bash
pip install antcrew
antcrew cobol-incident \
  "DB2 SQLCODE -803 en MOVPOL — duplicados en tabla MOVIMIENTOS durante rehabilitación de pólizas" \
  --cobol-dir ./cobol/src \
  --output-dir ./evidence
```

```
━━━━━━━━━━━━ AntCrew COBOL Incident Analysis ━━━━━━━━━━━━
Incident: DB2 SQLCODE -803 en MOVPOL — duplicados en tabla MOVIMIENTOS...
COBOL dir: ./cobol/src

┌──────────┬────────────┬────────────┬────────────┐
│ Programs │ CALL edges │ COPY edges │ DB2 tables │
├──────────┼────────────┼────────────┼────────────┤
│    5     │     6      │     6      │     5      │
└──────────┴────────────┴────────────┴────────────┘

━━━━━━━━━ Step 1: Incident Triage ━━━━━━━━━
Candidate programs: MOVPOL, REHPOL
Risk area: DB2 duplicate key on MOVIMIENTOS insert — likely missing idempotency check

┌──────────────────────────────────────────────────────┐
│  HUMAN REVIEW — Triage Result                        │
│  Candidates: MOVPOL, REHPOL                          │
│  Risk: DB2 duplicate key on MOVIMIENTOS insert       │
│  ¿Aprobar checkpoint 'triage_review'? [y/n]:         │
└──────────────────────────────────────────────────────┘

━━━━━━━━━ Step 2: Impact Analysis ━━━━━━━━━
Risk level: HIGH — DB2 writes + 3 transitive callers
Transitive callers: CALPRM, POLCAR, REHPOL
DB2 tables: MOVIMIENTOS(INSERT), POLIZAS(SELECT), POLIZAS(UPDATE)

━━━━━━━━━ Step 3: Structural COBOL Analysis ━━━━━━━━━
╭─ Analysis Summary ──────────────────────────────────╮
│ REHPOL calls MOVPOL without checking duplicate keys. │
│ WS-MESES-AJUSTE wraps at 12 months via MOD —        │
│ policies vencidas > 12 months get wrong adjustment.  │
╰──────────────────────────────────────────────────────╯

┌──────────────────────────────────────────────────────┐
│  HUMAN REVIEW — Approve evidence export?             │
│  Risk: HIGH | Callers: 3 | DB2 tables: 3             │
│  ¿Aprobar checkpoint 'analysis_review'? [y/n]:       │
└──────────────────────────────────────────────────────┘

Evidence package written: ./evidence/evidence.json
Verify with: antcrew verify ac_20261010_b9e1

Done.  Risk: HIGH | Programs: MOVPOL, REHPOL | HITL decisions: 2
```

The evidence package is a JSON file with the triage, impact analysis, structural findings, and every HITL decision — hash-chained and verifiable with `antcrew verify`.

---

## The problem

AI agents can now write code, query systems, and execute tools.

The problem for teams isn't capability. It's control.

When a single agent holds all the context and makes every decision unilaterally:

- There is no checkpoint where a human can say "stop — show me the plan first"
- The same agent that writes the migration also decides it passes review
- Nothing records *who* authorized a change, *what* was approved, or *why*

AntCrew adds a governance layer: human approval gates, policy checks, typed artifacts, and a tamper-evident record of every event in the execution.

---

## How it works

Every execution follows the same governed flow:

| Phase | What happens |
|---|---|
| **Request** | Task submitted — scope, risk, affected systems identified |
| **→ Human gate 1** | Plan shown to reviewer before any work starts |
| **Execution** | Agents work within approved scope |
| **→ Human gate 2** | Output reviewed before any side effects (commits, API calls) |
| **Evidence** | Tamper-evident record: approvals, agents, models, cost, artifacts |

This flow applies whether the task is a code change, a legal review, a data migration, or any other work where the outcome matters.

---

## Human-in-the-loop

The approval gate is the central feature. Every pipeline has at least two checkpoints:

**1 — Plan approval** (before any work is done)

```
PLAN READY FOR REVIEW

  Scope:     Add subscription support
  Approach:  New Subscription model + Stripe webhook + service layer
  Affected:  12 files (3 new, 9 modified, 0 deleted)
  Migration: Yes — creates subscriptions table
  Estimated: Medium complexity

  Agents queued:
    ✓ BackendDev  ✓ QA  ✓ Reviewer  ✓ DocWriter

Approve plan? [A]pprove / [R]equest changes / [X] Reject
```

**2 — Output approval** (after agents finish, before side effects)

```
READY TO PUSH

  142 tests passed (0 failing)
  Reviewer: no remaining issues
  Files: 12 changed, +487 / -34 lines

  Generated PR description and change summary attached.

Approve PR? [A]pprove / [R]equest changes / [X] Reject
```

Every approval is written to the TraceLog with reviewer identity, timestamp, and row hash. The TraceLog records every execution event — run start, each agent call, every HITL decision, run end — in a SHA-256 hash chain. Any modification, deletion, or insertion breaks the chain and is detectable.

**Remote approvals (antcrew-platform)**

When reviewers are not at a terminal, approvals route through antcrew-platform:

```
AntCrew is requesting your approval

  WHAT:   Add subscription support to payments-service
  PLAN:   12 files · Medium complexity · $~1.50 estimated
  RISKS:  Schema migration (reversible)

  [ APPROVE PLAN ]   [ REQUEST CHANGES ]   [ REJECT ]
```

---

## Evidence

Every completed execution produces a verifiable evidence package:

```bash
antcrew inspect ac_20261007_a3f2      # full trace: agents, decisions, cost, hashes
antcrew verify  ac_20261007_a3f2      # verify execution chain integrity
antcrew evidence ac_20261007_a3f2 --html evidence.html  # export HTML report
antcrew runs                          # list recent governed executions
```

Each evidence record includes:
- Agent names, models, and per-call token counts
- Typed output artifacts (Pydantic objects)
- Every HITL decision: reviewer identity + timestamp + verdict
- SHA-256 execution event chain (run_started → agent_call → hitl_decision → run_ended)
- Chain root hash — a single fingerprint of the complete execution

The chain covers every execution event in sequence order. Modifying any field, deleting any event, or inserting an event into the middle breaks the chain. `antcrew verify` checks it without network access or external dependencies.

---

## Why antcrew

| | antcrew | Claude Code | CrewAI |
|---|---|---|---|
| Human approval gates | ✓ hash-chained record | ✗ | ✗ |
| Governed execution flow | ✓ | ✗ | ✗ |
| Typed output contracts | ✓ Pydantic | ✗ | ✗ |
| Trace & replay | ✓ SQLite TraceLog | ✗ | ✗ |
| Works 100% offline | ✓ Ollama | ✓ | partial |
| Compliance exports | ✓ ZIP + hash chain | ✗ | ✗ |
| Issue → PR in one command | ✓ `antcrew issue` | manual | ✗ |

---

## Quick start

**Zero setup — simulated LLM, no credentials:**

```bash
pip install antcrew
antcrew run --model simulated "Build a REST API for user authentication"
```

**Fully local — Ollama (no API key, no data leaves your machine):**

```bash
antcrew run --model ollama:llama3 "Add OAuth2 to this repo" --project-dir .
```

**Cloud model:**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
antcrew issue owner/repo#143
```

**From Python:**

```python
from antcrew import DevTeam

team = DevTeam()
result = team.run("Add a /users/{id} endpoint with tests")
print(result.state["code_artifacts"])   # typed Pydantic objects
print(result.usage.total_usd)           # exact cost
```

---

## The canonical flow: `antcrew issue`

`antcrew issue` is the main entry point for engineering work. Give it a GitHub issue; it returns a reviewed, human-approved PR.

```bash
antcrew issue owner/repo#143
antcrew issue owner/repo#143 --model ollama:llama3   # fully local
antcrew issue owner/repo#143 --auto-approve          # no HITL gates (CI use)
```

| Phase | What happens |
|---|---|
| **Discovery** | Reads the issue, analyzes the repo, identifies affected modules |
| **Plan** | Scoped implementation plan (files, approach, migration needs) |
| **→ Human gate 1** | Shows plan — waits for approval before writing any code |
| **Implement** | BackendDev (+ FrontendDev if needed) generates the changes |
| **Test** | Runs existing test suite + generates new tests; auto-retries failures |
| **Review** | Reviewer agent checks quality, security, and spec compliance |
| **→ Human gate 2** | Shows review summary — waits for approval before opening PR |
| **PR** | Pushes branch and opens PR with generated description |

At the end:

```
DONE

  PR:     github.com/owner/repo/pull/221
  Cost:   $1.84
  Time:   9m 12s
  Files:  12 changed (+487 / -34)
  Tests:  142 passed, 0 failing
  Review: 2 findings, both fixed
  HITL:   2 approvals (plan + PR)

  Trace:  antcrew inspect ac_20261007_a3f2
```

---

## Built-in workflows

For pipelines beyond `antcrew issue`:

| Team | Best for |
|---|---|
| `DevTeam` | Backend features, APIs, services |
| `FullStackTeam` | Frontend + backend + tests + docs in one pass |
| `LegalReviewTeam` | Contract review with risk scoring |
| `CodeMigrationTeam` | Automated codebase migration (Python, Java, COBOL) |
| `CustomTeam` | Any pipeline you define in Python or YAML |

```python
from antcrew import DevTeam, FullStackTeam, LegalReviewTeam

# Backend feature with trace
from antcrew.trace import TraceLog
trace = TraceLog("~/.antcrew/trace.db")
result = DevTeam(trace_log=trace).run("Add rate limiting to /api/v1")

# Contract review
finding = LegalReviewTeam().run(nda_text).state["legal_finding"]
print(f"High-risk clauses: {finding.high_risk_count}")
```

---

## CLI reference

```bash
antcrew issue owner/repo#143        # governed: GitHub issue → reviewed PR
antcrew cobol-incident "desc" --cobol-dir ./src  # COBOL incident: triage → impact → HITL → evidence
antcrew run "goal"                  # run a pipeline locally
antcrew run "goal" --model ollama:llama3  # offline, no API key
antcrew init                        # scaffold a new project interactively
antcrew inspect <run-id>            # view trace: agents, decisions, cost, hashes
antcrew verify <run-id>             # verify hash chain integrity
antcrew trace replay <run-id>       # replay all agent calls
antcrew eval                        # run EvalSuite regression tests
antcrew describe                    # show pipeline data flow
antcrew serve                       # local web dashboard
antcrew cost                        # usage and cost summary
antcrew dag                         # visualize agent graph
```

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

`pip install antcrew` ships two layers in a single wheel:

| Layer | What it does |
|---|---|
| `antcrew` | Named-role pipelines (BA, PM, Dev…) orchestrated with LangGraph. HITL, sessions, memory. |
| `antcrew_engine` | Goal-directed `EngineLoop` — capabilities selected at runtime until conditions are satisfied. Bundled since v0.35.0. |

---

## antcrew-platform (optional)

The SDK runs entirely locally. **antcrew-platform** is the optional managed layer for teams that need:

- **Remote HITL** — reviewers approve from Slack or a web link, not a terminal
- **Multi-workspace** concurrent runs with cost roll-up and billing
- **Dashboard** — live run stream, eval trends, cost charts
- **Compliance exports** — ZIP with attestations, DPA templates, hash chain for auditors
- **GitHub App** — auto-post explainability comments on PRs
- **SSO** — GitHub OAuth2, SAML (Team+ license)

[→ antcrew-platform](https://github.com/iagop03/antcrew-platform) · [→ docs](https://antcrew.org/docs/)

---

## License

[Business Source License 1.1](LICENSE) — source available, free for internal use.

Internal use (running antcrew agents and pipelines within your own organization) is free. Providing antcrew as a hosted or managed service to third parties requires a commercial license. Converts to Apache 2.0 on 2030-01-01.

Contact iago.pueyo99@gmail.com for commercial licensing.
