# antcrew

[![CI](https://github.com/iagop03/antcrew/actions/workflows/ci.yml/badge.svg)](https://github.com/iagop03/antcrew/actions)
[![PyPI](https://img.shields.io/pypi/v/antcrew)](https://pypi.org/project/antcrew/)
[![Python](https://img.shields.io/pypi/pyversions/antcrew)](https://pypi.org/project/antcrew/)
[![SDK License: Apache 2.0](https://img.shields.io/badge/SDK%20License-Apache%202.0-blue.svg)](LICENSE)
[![Platform License: ELv2](https://img.shields.io/badge/Platform%20License-ELv2-orange.svg)](platform/LICENSE)

**AntCrew is the manager. Your LLM is the worker.**

From a GitHub issue to a reviewed, approvable PR — with full audit trail.

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
  Cost: $1.84 · Time: 9m 12s · Human interventions: 2
```

---

## The problem antcrew solves

Claude Code, Codex, and Cursor are excellent at writing code. They are single-agent tools: one context, one decision-maker, one pass.

That works well for simple tasks. It breaks down when a change is complex enough that you do not want one agent to hold all the context and make every call unilaterally:

- The same agent that writes the migration also decides it passes review
- There is no checkpoint where a human can say "stop — I want to see the plan before you touch anything"
- Nothing records *why* a decision was made, only *what* was produced

AntCrew adds the management layer on top: discovery, planning, implementation, testing, review, and two human approval gates — all coordinated, all traced.

---

## Human-in-the-loop

The approval gate is the central feature. Every pipeline has at least two checkpoints:

**1 — Plan approval** (before any code is written)

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

**2 — PR approval** (after review, before the branch is pushed)

```
READY TO PUSH

  142 tests passed (0 failing)
  Reviewer: no remaining issues
  Files: 12 changed, +487 / -34 lines

  Generated PR description and change summary attached.

Approve PR? [A]pprove / [R]equest changes / [X] Reject
```

Every approval is recorded in the TraceLog with reviewer identity and timestamp. In regulated environments, this creates an auditable record of who authorized each change and when.

**Remote review (antcrew-platform)**

For teams where reviewers are not at a terminal, approvals route through antcrew-platform and arrive as Slack messages or web links:

```
AntCrew is requesting your approval

  WHAT:   Add subscription support to payments-service
  PLAN:   12 files · Medium complexity · $~1.50 estimated
  RISKS:  Schema migration (reversible)

  [ APPROVE PLAN ]   [ REQUEST CHANGES ]   [ REJECT ]
```

---

## Why antcrew

| | antcrew | Claude Code | CrewAI |
|---|---|---|---|
| Multi-agent coordination | ✓ | ✗ | ✓ |
| Human approval gates | ✓ tamper-evident log | ✗ | ✗ |
| Typed output contracts | ✓ Pydantic | ✗ | ✗ |
| Trace & replay | ✓ SQLite TraceLog | ✗ | ✗ |
| Works 100% offline | ✓ Ollama | ✓ | partial |
| Audit log (regulated sectors) | ✓ hash chain | ✗ | ✗ |
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

`antcrew issue` is the main entry point. Give it a GitHub issue; it returns a PR.

```bash
antcrew issue owner/repo#143
antcrew issue owner/repo#143 --model ollama:llama3   # fully local
antcrew issue owner/repo#143 --auto-approve          # no HITL gates (CI use)
```

What it does:

| Phase | What happens |
|---|---|
| **Discovery** | Reads the issue, analyzes the repo structure, identifies affected modules |
| **Plan** | Produces a scoped implementation plan (files, approach, migration needs) |
| **→ Human gate 1** | Shows plan, waits for approval before writing any code |
| **Implement** | BackendDev (and FrontendDev if needed) generates the changes |
| **Test** | Runs existing test suite + generates new tests; auto-retries failures |
| **Review** | Reviewer agent checks for quality, security, and spec compliance |
| **→ Human gate 2** | Shows review summary, waits for approval before opening PR |
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

## Teams

For custom pipelines beyond `antcrew issue`:

| Team | Best for |
|---|---|
| `DevTeam` | Backend features, APIs, services |
| `FullStackTeam` | Frontend + backend + tests + docs in one pass |
| `LegalReviewTeam` | Contract review with risk scoring |
| `CodeMigrationTeam` | Automated codebase migration (Python, Java, COBOL) |
| `CustomTeam` | Any pipeline you define in Python or YAML |

```python
from antcrew import DevTeam, FullStackTeam, LegalReviewTeam

# Backend feature
result = DevTeam().run("Add rate limiting to /api/v1", project_dir="./src")

# Full-stack
result = FullStackTeam().run("Add a subscription billing page")

# Contract review
finding = LegalReviewTeam().run(nda_text).state["legal_finding"]
print(f"High-risk clauses: {finding.high_risk_count}")
```

---

## Audit trail (regulated environments)

Every agent decision is recorded to a local SQLite TraceLog:

```bash
antcrew inspect ac_20261007_a3f2      # full trace: prompts, tokens, cost, hashes
antcrew trace replay ac_20261007_a3f2 # replay call-by-call
antcrew trace --verify-chain          # verify tamper-evident hash chain
```

Each TraceLog entry includes:
- Agent name, model, and prompt hash
- Output artifact (typed Pydantic object)
- HITL decisions: reviewer identity + timestamp + approval/rejection
- SHA-256 chain linking every entry to the previous one

This chain can be exported (`GET /compliance/hash-chain`) and verified by an external auditor without accessing the LLM or source code.

---

## CLI reference

```bash
antcrew issue owner/repo#143        # canonical: GitHub issue → PR
antcrew run "goal"                  # run a team locally
antcrew run "goal" --model ollama:llama3  # offline, no API key
antcrew init                        # scaffold a new project interactively
antcrew inspect <run-id>            # view trace: prompts, tokens, cost, hashes
antcrew trace replay <run-id>       # replay all agent calls
antcrew trace --verify-chain        # verify hash chain integrity
antcrew eval                        # run EvalSuite regression tests
antcrew describe                    # show pipeline data flow
antcrew serve                       # local web dashboard
antcrew cost                        # usage and cost summary
antcrew dag                         # visualize agent graph
```

Run `antcrew --help` for the full list of commands.

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
| `antcrew` | Named-role teams (BA, PM, Dev…) orchestrated with LangGraph. HITL, sessions, memory. |
| `antcrew_engine` | Goal-directed `EngineLoop` — capabilities selected at runtime until conditions are satisfied. Bundled since v0.35.0. |

---

## antcrew-platform (optional SaaS layer)

The SDK runs entirely locally. **antcrew-platform** is the optional managed layer for teams that need:

- **Remote HITL** — reviewers approve from Slack or a web link, not a terminal
- **Multi-workspace** concurrent runs with cost roll-up and billing
- **Dashboard** — live run stream, eval trends, cost charts
- **Compliance exports** — ZIP with attestations, DPA templates, hash chain for auditors
- **GitHub App** — auto-post explainability comments on PRs
- **SSO** — GitHub OAuth2, SAML (Team+ license)

[→ antcrew-platform](https://github.com/iagop03/antcrew-platform)

---

## License

**antcrew SDK** (`antcrew/`, `antcrew_engine/`) — [Apache License 2.0](LICENSE). Free to use, modify, and distribute, including in commercial products.

**antcrew-platform** (`platform/`) — [Elastic License 2.0](platform/LICENSE). Free for self-hosted use within your own organization. Providing it as a hosted/managed service to third parties requires a commercial agreement.
