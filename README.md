# AntCrew

Multi-agent framework for software development teams, built on [LangGraph](https://github.com/langchain-ai/langgraph). LLM-agnostic — run with any cloud model or fully local.

---

## What is it?

AntCrew spins up a team of specialized AI agents — Business Analyst, PM, Backend Dev, Frontend Dev, QA, Reviewer, DevOps, Doc Writer — that collaborate through a shared state graph to take a project from a one-line description to code, tests, CI config, and documentation.

Designed for a specific sweet spot: **new MVPs, small-to-medium projects, and isolated new modules inside existing systems.** Not aimed at maintaining large legacy codebases.

Key ideas:

- **LLM-agnostic.** Anthropic, OpenAI, Gemini, Groq, Ollama, or any OpenAI-compatible endpoint. Mix models per agent.
- **Local-first option.** Run entirely on your machine with Ollama — no API keys, no code leaves your network.
- **Project sessions.** Each run builds on the previous one — tickets, code, and docs accumulate across multiple sessions instead of starting from scratch every time.
- **Persistent LLM cache.** SQLite-backed cache avoids redundant API calls across runs — crucial for iterative development.
- **Human-in-the-loop by design.** Interactive mode pauses after every agent for approve / reject / edit / feedback.
- **Conversational refinement.** Type free-text feedback; agents revise their output in-place before the pipeline continues.
- **Typed artifacts.** PRDs, tickets, code changes, test suites, DevOps configs, and docs are Pydantic objects — predictable, auditable, and serializable.
- **Real sandbox execution.** Generated tests run in a subprocess (or Docker container) and results feed back into the state.
- **Real-time streaming.** Watch tokens arrive token-by-token in the terminal or the web dashboard.
- **Semantic memory.** Agents reference decisions from past runs via a vector store (ChromaDB or in-memory).
- **Retry + resilience.** Automatic exponential-backoff retry on timeouts, rate limits, and transient errors.
- **Token tracking.** Per-agent input/output counts and estimated cost shown after every run.
- **Web dashboard.** React SPA served from `antcrew serve` — start runs, watch the pipeline live, browse artifacts.
- **Integrations.** Push tickets to Jira, open PRs on GitHub, publish docs to Confluence, send reviews to Slack or Telegram.

---

## Quick start

```python
from antcrew import DevTeam
from antcrew.models import SimulatedLLM   # no API key needed

team = DevTeam(model=SimulatedLLM())
state = team.run("Add a password reset flow to the auth module")

for artifact in state["code_artifacts"]:
    print(artifact.file_path, "—", artifact.description)
```

```python
# With real models
from antcrew import DevTeam
from antcrew.models import AnthropicModel, OllamaModel

team = DevTeam(model=AnthropicModel("claude-sonnet-4-6"))  # cloud
team = DevTeam(model=OllamaModel("llama3"))                # fully local

state = team.run("Build a REST API for user authentication")
```

---

## Teams

| Team | Agents | Best for |
|---|---|---|
| `DevTeam` | BA → PM → BackendDev | Backend features, APIs |
| `FullStackTeam` | BA → PM → Backend → Frontend → QA → Reviewer → DevOps → DocWriter | Full-stack MVPs |
| `ResearchTeam` | Researcher → Writer | Technical research, blog posts |
| `ContentTeam` | Idea → Copywriter → Editor | Marketing content, docs |

```python
from antcrew import DevTeam, FullStackTeam, ResearchTeam, ContentTeam
```

---

## Models

| Model string (YAML / CLI) | Python class | Notes |
|---|---|---|
| `claude` / `claude-sonnet-4-6` | `AnthropicModel` | Default |
| `gpt-4o` / `gpt-4o-mini` | `OpenAIModel` | Any OpenAI model |
| `gemini` / `gemini-1.5-pro` | `GeminiModel` | Google Gemini via REST |
| `groq:llama3-70b-8192` | `GroqModel` | Groq ultra-fast inference |
| `ollama:llama3` | `OllamaModel` | Local via Ollama |
| `simulated` | `SimulatedLLM` | Fixtures, CI, demos — no API |

### OpenAI-compatible APIs (DeepSeek, Mistral, LM Studio, llama.cpp)

```python
from antcrew.models import OpenAIModel

team = DevTeam(model=OpenAIModel(
    model="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_key="...",
))
```

### Mix models per agent

```python
team = DevTeam(
    model=OllamaModel("llama3"),               # default for all agents
    agents={
        "pm": PMAgent(llm=AnthropicModel()),   # override PM with Claude
    },
)
```

### Fallback chains

Automatically falls back to the next model if one fails (rate limit, timeout, API error):

```python
from antcrew.models import FallbackLLM, AnthropicModel, OllamaModel

llm = FallbackLLM([
    AnthropicModel("claude-sonnet-4-6"),   # try first
    OllamaModel("llama3"),                 # fall back to local
])

team = DevTeam(model=llm)
```

---

## CLI

```
antcrew run          Run a pipeline autonomously
antcrew interactive  Run with human-in-the-loop review after every agent
antcrew project      Manage persistent project sessions
antcrew eval         Run evaluation cases and score results
antcrew serve        Start the REST API + web dashboard
antcrew show         Display a previously saved state file
antcrew init         Generate a starter agentteam.yaml + main.py
antcrew flow         Validate and inspect flow config files
```

### `antcrew run`

```bash
antcrew run "Build a user authentication module"
antcrew run "Add a password reset flow" --team dev --model claude
antcrew run "Research serverless databases" --team research --model gpt-4o
antcrew run "Write a blog post about Rust" --team content --model ollama:llama3
antcrew run "Build a full-stack todo app" --team fullstack

# Tokens stream in real time (default on)
antcrew run "..." --stream          # default
antcrew run "..." --no-stream       # spinner only

# Save final state for later inspection
antcrew run "..." --save state.json
antcrew show state.json

# Use a YAML/JSON config file
antcrew run "..." --config agentteam.yaml

# Persistent project session — state accumulates across runs
antcrew run "Build JWT auth" --team dev --project auth.json
antcrew run "Add OAuth"               --project auth.json  # continues from run 1
antcrew run "Fix refresh token bug"   --project auth.json  # continues from run 2

# Persistent LLM cache — avoids repeated API calls
antcrew run "Build JWT auth" --team dev --cache ~/.antcrew/cache.db

# Combine both — full iterative workflow
antcrew run "Build JWT auth" --team dev --project auth.json --cache ~/.antcrew/cache.db
```

### `antcrew interactive`

Pauses after every agent and prompts for a decision:

```bash
antcrew interactive "Build a login module" --team dev
```

At each pause you can:
- `approve` — continue to the next agent
- `reject` — stop the pipeline
- `edit` — open the artifact in `$EDITOR` as JSON
- *any other text* — send as feedback; agents with `conversational = True` revise their output in-place

### `antcrew project`

Manage persistent projects across multiple sessions:

```bash
# Start a new project (--team required on first run)
antcrew project run auth.json "Build JWT authentication" --team dev --model claude

# Continue the same project (team reused from stored spec)
antcrew project run auth.json "Add OAuth with Google"
antcrew project run auth.json "Fix refresh token expiry"

# With persistent LLM cache
antcrew project run auth.json "Add rate limiting" --cache ~/.antcrew/cache.db

# Inspect accumulated state (PRD, all tickets, all code files)
antcrew project show auth.json
antcrew project show auth.json --json   # raw JSON

# Show run history as a table
antcrew project history auth.json
```

Each run enriches the request with context from previous runs so agents build on prior work rather than starting over.

### `antcrew eval`

Run evaluation cases and score the pipeline automatically:

```bash
# Single case
antcrew eval "Build a login module" --team dev --model claude

# Batch from JSON
antcrew eval cases.json --team dev --model claude
antcrew eval cases.json --model claude --judge claude  # LLM judge scoring
antcrew eval cases.json --output results.json
```

`cases.json` format:

```json
[
  {
    "name": "auth module",
    "request": "Build a REST API with JWT authentication",
    "expect_min_tickets": 3,
    "expect_min_code_files": 2,
    "expect_review_verdict": "approve"
  }
]
```

### `antcrew flow`

Validate and inspect pipeline flows defined in YAML/JSON:

```bash
antcrew flow show  pipeline.yaml    # pretty-print the flow graph
antcrew flow validate pipeline.yaml # validate without running
```

### `antcrew serve`

```bash
antcrew serve                          # http://0.0.0.0:8000
antcrew serve --host 127.0.0.1 --port 9000
antcrew serve --reload                 # auto-reload for development
```

Requires `pip install antcrew[server]`.

REST endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/run` | Start a pipeline (background task) |
| `GET` | `/run/{id}` | Poll status and state |
| `GET` | `/run/{id}/stream` | Server-Sent Events: live tokens + done/error |
| `GET` | `/run/{id}/artifacts` | Get artifacts + test results once done |
| `GET` | `/runs` | List all runs |
| `DELETE` | `/run/{id}` | Remove a run |
| `POST` | `/eval` | Start an eval run |
| `GET` | `/eval/{id}` | Get eval result |

Interactive docs at `http://localhost:8000/docs`.

---

## YAML / JSON Configuration

Run the same config repeatedly — each call picks up where the previous one left off:

```yaml
# agentteam.yaml
team: dev                        # dev | fullstack | research | content
model: claude                    # default model for all agents

# Persistent LLM cache — avoids repeated API calls during development
cache: ~/.antcrew/cache.db

# Persistent project session — state accumulates across runs
project: ./auth-service.json

# Sandbox for executing generated tests
runner:
  type: local                    # local | docker
  timeout: 60

# Docker sandbox (real isolation, zero host writes)
# runner:
#   type: docker
#   image: python:3.12-slim
#   requirements: [pytest, requests, pydantic]
#   timeout: 120
#   memory: 512m
#   network: none

# Custom pipeline order
flow:
  - [business_analyst, pm]
  - [pm, backend_dev]
  - [backend_dev, qa]

# Notification channels
channel:
  type: telegram                 # telegram | slack | console
  token: ${BOT_TOKEN}
  chat_id: ${CHAT_ID}

# Per-agent overrides
agents:
  pm:
    model: claude-sonnet-4-6
    approval_required: true
  backend_dev:
    model: ollama:llama3
  reviewer:
    model: gpt-4o
```

```bash
antcrew run "Build auth" --config agentteam.yaml
# First run: creates auth-service.json + cache.db
# Second run: loads project, enriches request with prior context, reuses cache
```

JSON is also supported natively (no extra dependencies).

Environment variables are expanded with `${VAR}` syntax.

Generate a starter config:

```bash
antcrew init --template dev_team
antcrew init --template fullstack_team
antcrew init --template research_team --output ./my-team
```

Load programmatically:

```python
from antcrew.config import load, load_context

team = load("agentteam.yaml")             # returns configured team
ctx  = load_context("agentteam.yaml")     # returns TeamContext(team, project)

# If config has project: key, ctx.project is a ready Project instance
ctx.project.run("Build auth")
```

---

## Project Sessions

Without `Project`, every `team.run()` starts from scratch. With it, runs accumulate:

```python
from antcrew import Project, DevTeam
from antcrew.models import AnthropicModel

llm  = AnthropicModel()
team = DevTeam(model=llm)

project = Project(team, name="auth-service", path="auth.json")

project.run("Build JWT authentication")       # run 1 → PRD + 3 tickets + 2 files
project.run("Add OAuth with Google")          # run 2 → agents see run 1's output
project.run("Fix refresh token expiry bug")   # run 3 → 9 tickets, 6 files total

print(len(project.state["tickets"]))          # 9
print(len(project.state["code_artifacts"]))   # 6
print(project.summary())
```

State is auto-saved to `auth.json` after every run. Resume later:

```python
from antcrew import Project

project = Project.load("auth.json")           # team restored from stored spec
project.run("Add rate limiting")              # continues from where you left off
```

Combine with `FileLLMCache` for the full iterative workflow:

```python
from antcrew import Project, DevTeam, FileLLMCache
from antcrew.models import AnthropicModel

llm = AnthropicModel()
llm.with_cache("~/.antcrew/cache.db")        # reuse API responses across runs

project = Project(DevTeam(model=llm), path="auth.json")
project.run("Build JWT auth")   # makes API calls, caches responses
project.run("Add OAuth")        # cache hits for unchanged prompts → cheaper + faster
```

---

## LLM Cache

Avoid paying for the same API call twice. Especially valuable during iterative development when multiple runs share similar prompts.

```python
from antcrew import LLMCache, FileLLMCache
from antcrew.models import AnthropicModel

llm = AnthropicModel()

# In-memory cache (cleared on process restart)
llm.with_cache()

# SQLite cache (persists across restarts)
llm.with_cache("~/.antcrew/cache.db")

# Or pass an instance directly
llm.with_cache(FileLLMCache("~/.antcrew/cache.db"))

team = DevTeam(model=llm)
team.run("Build auth")   # API called, response stored
team.run("Build auth")   # served from cache — 0 tokens, 0 cost
```

Cache stats are shown automatically at the end of `antcrew run --cache`:

```
Cache: 8 hits / 3 misses (73% hit rate)
```

---

## Sandbox / Test Runner

AntCrew can execute the generated tests and feed results back into the pipeline:

```python
from antcrew import DevTeam
from antcrew.sandbox import LocalRunner, DockerRunner

# Run in a temp directory on the host
runner = LocalRunner(timeout=60)

# Run in a fresh Docker container (real isolation)
runner = DockerRunner(
    image="python:3.12-slim",
    requirements=["pytest", "requests"],
    timeout=120,
    memory="512m",
    network="none",          # no network access inside container
)

team = DevTeam(model=llm, runner=runner)
state = team.run("Build a validated user registration API")

tr = state.get("test_results")
if tr:
    print(f"Tests: {tr.passed} passed / {tr.failed} failed in {tr.duration_ms:.0f}ms")
    if not tr.success:
        print(tr.output)     # full pytest output
```

`DockerRunner` creates a fresh container per run, copies artifacts via stdin (no volume mounts), and always removes the container when done — zero host filesystem writes.

---

## Flows in YAML / JSON

Define custom pipeline graphs without writing Python:

```yaml
# pipeline.yaml
name: extended-dev
agents:
  - business_analyst
  - pm
  - backend_dev
  - frontend_dev
  - qa
  - reviewer

edges:
  - [business_analyst, pm]
  - [pm, backend_dev]
  - [pm, frontend_dev]      # parallel tracks
  - [backend_dev, qa]
  - [frontend_dev, qa]
  - [qa, reviewer]

entry_point: business_analyst
```

```python
from antcrew.flow import load_flow, validate_flow

flow = load_flow("pipeline.yaml")   # also accepts .json
validate_flow(flow)                  # raises on invalid edges / missing agents
```

```bash
antcrew flow show pipeline.yaml
antcrew flow validate pipeline.yaml
```

---

## Web Dashboard

A React SPA served directly from `antcrew serve` at `/ui/`.

**Features:**
- Start new runs with team + model selector
- Live pipeline progress — per-agent chips animate as each agent runs
- Token stream colored by agent in real time
- Collapsible artifact browser — PRD, tickets, code files with copy button, review findings, docs
- Token usage table with per-agent cost estimate

**Build once, then it persists:**

```bash
# First time (requires Node.js 18+)
cd dashboard
npm install
npm run build        # → writes to antcrew/static/

# Start the server
antcrew serve        # → Dashboard at http://localhost:8000/ui/
```

**Local development** (hot-reload):

```bash
antcrew serve &                  # FastAPI on :8000
cd dashboard && npm run dev      # Vite on :5173 with proxy to :8000
```

---

## Streaming

All six LLM adapters support token-by-token streaming. The `on_token` callback fires for each chunk:

```python
llm = AnthropicModel()
llm.on_token = lambda token: print(token, end="", flush=True)

team = DevTeam(model=llm)
team.run("Build a login module")
```

The CLI streams by default (`--no-stream` to disable). The API server exposes an SSE endpoint:

```javascript
const es = new EventSource('/run/{id}/stream')
es.onmessage = (e) => {
    const { agent, token } = JSON.parse(e.data)
    // append token to UI colored by agent
}
es.addEventListener('done', (e) => {
    const { state, usage } = JSON.parse(e.data)
})
```

---

## Semantic Memory

Agents can reference decisions from previous runs using a vector store. The Business Analyst, PM, and Reviewer automatically inject relevant past context into their prompts.

```python
from antcrew import DevTeam, ChromaMemory

# Persistent memory stored in .antcrew_memory/ (survives restarts)
memory = ChromaMemory()

team = DevTeam(model=llm, memory=memory)

# Run 1 — artifacts are stored automatically after the pipeline finishes
state = team.run("Build an auth module with JWT")

# Run 2 — BA, PM, and Reviewer see relevant context from run 1
state = team.run("Extend auth with OAuth2 SSO")
```

**Backends:**

| Class | Description | Install |
|---|---|---|
| `InMemoryMemory` | Jaccard word-overlap. No dependencies. Good for demos and tests. | (included) |
| `ChromaMemory` | Embeddings via ChromaDB. Persistent to disk. Recommended. | `pip install antcrew[memory]` |

```python
from antcrew import InMemoryMemory, ChromaMemory

mem = InMemoryMemory()                         # ephemeral, no deps
mem = ChromaMemory()                           # persistent in .antcrew_memory/
mem = ChromaMemory(path="/data/db", collection="project_x")

# Search manually
results = mem.search("authentication JWT tokens", n=5)
for r in results:
    print(r.score, r.text[:100])

# Store a completed run
mem.store_run(state, run_id="v1", project="myapp")
```

---

## Token Tracking

Every LLM call records input tokens, output tokens, and estimated cost. Shown automatically at the end of `antcrew run`:

```
Token usage
┌───────────────────┬──────────────────┬──────────┬───────────┬───────────┐
│ Agent             │ Model            │ In tok   │ Out tok   │ Cost USD  │
├───────────────────┼──────────────────┼──────────┼───────────┼───────────┤
│ business_analyst  │ claude-sonnet-4-6 │   1,240  │     480   │  $0.0111  │
│ pm                │ claude-sonnet-4-6 │   2,100  │     920   │  $0.0201  │
│ backend_dev       │ claude-sonnet-4-6 │   3,800  │   2,400   │  $0.0474  │
│ Total             │                  │   7,140  │   3,800   │  $0.0786  │
└───────────────────┴──────────────────┴──────────┴───────────┴───────────┘
```

Access programmatically:

```python
llm = AnthropicModel()
team = DevTeam(model=llm)
team.run("Build X")

summary = llm.get_usage_summary()
print(summary["total_cost_usd"])      # e.g. 0.0786
print(summary["by_agent"])            # list of per-agent dicts
```

---

## Retry + Resilience

`BaseLLM` wraps every non-streaming call in exponential-backoff retry. Retries on timeouts, rate limits (HTTP 429), and transient server errors (5xx):

```python
llm = AnthropicModel()
llm.max_retries = 3       # default
llm.retry_delay = 1.0     # seconds before first retry (doubles each attempt)
llm.timeout = 120.0       # per-call timeout in seconds
```

Streaming calls are not retried (partial tokens already emitted would duplicate).

---

## Integrations

### Jira

```python
from antcrew.integrations import JiraIntegration

jira = JiraIntegration(
    url="https://myorg.atlassian.net",
    email="dev@myorg.com",
    api_token=os.environ["JIRA_TOKEN"],
    project_key="DEV",
)
pairs = jira.sync_tickets(state["tickets"])
# → [(<Ticket>, "DEV-42"), ...]
```

### GitHub

```python
from antcrew.integrations import GitHubIntegration

gh = GitHubIntegration(token=os.environ["GH_TOKEN"], repo="myorg/myapp")
pr_url = gh.create_pr(state)
# Creates branch, upserts files, opens PR
```

### Confluence

```python
from antcrew.integrations import ConfluenceIntegration

confluence = ConfluenceIntegration(
    url="https://myorg.atlassian.net",
    email="dev@myorg.com",
    api_token=os.environ["CONFLUENCE_TOKEN"],
)
confluence.publish_prd(state, space_key="ENG")
confluence.publish_docs(state, space_key="ENG", parent_title="Projects")
```

### Slack / Telegram

```python
from antcrew.integrations import SlackChannel
from antcrew import DevTeam

team = DevTeam(
    model=AnthropicModel(),
    integrations=[SlackChannel(
        bot_token=os.environ["SLACK_BOT_TOKEN"],
        app_token=os.environ["SLACK_APP_TOKEN"],
        channel_id="#dev-reviews",
    )],
)
state = team.run_interactive("Build feature X")
```

---

## Persistence

```python
from antcrew import save_state, load_state

state = team.run("Build X")
save_state(state, "run_2024.json")

# Later — reload and browse artifacts
raw = load_state("run_2024.json")
```

```bash
antcrew run "Build X" --save run_2024.json
antcrew show run_2024.json
```

---

## Installation

```bash
pip install antcrew
```

### Optional extras

```bash
pip install "antcrew[server]"     # FastAPI + uvicorn for antcrew serve
pip install "antcrew[dashboard]"  # server + aiofiles for the web dashboard
pip install "antcrew[memory]"     # ChromaDB for semantic memory
pip install "antcrew[telegram]"   # Telegram HITL channel
pip install "antcrew[all]"        # everything above
```

### Model packages (install only what you use)

```bash
pip install anthropic          # AnthropicModel  (ANTHROPIC_API_KEY)
pip install openai             # OpenAIModel — also DeepSeek, Mistral, LM Studio
pip install groq               # GroqModel       (GROQ_API_KEY)
# GeminiModel and OllamaModel use httpx (already included in core)
```

### Install from source

```bash
git clone https://github.com/iagop03/antcrew.git
cd antcrew
pip install -e ".[dev]"
```

---

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

The full suite uses `SimulatedLLM` — no API keys required. 742 tests, no live network calls.

---

## License

[MIT](LICENSE)
