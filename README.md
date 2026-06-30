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
- **Explicit contracts.** Each agent declares `consumes` and `produces` — `antcrew describe` shows the full pipeline data flow without reading source code.
- **Real sandbox execution.** Generated tests run in a subprocess (or Docker container) and results feed back into the state.
- **Real-time streaming.** Watch tokens arrive token-by-token in the terminal or the web dashboard.
- **Semantic memory.** Agents reference decisions from past runs via a vector store (ChromaDB or in-memory).
- **Retry + resilience.** Automatic exponential-backoff retry on timeouts, rate limits, and transient errors.
- **Token tracking.** Per-agent input/output counts and estimated cost shown after every run.
- **Web dashboard.** React SPA served from `antcrew serve` — start runs, watch the pipeline live, browse artifacts.
- **Artifact tracking.** Every code file, test, and DevOps artifact is tagged with the run number that created it — full provenance across multi-run projects.
- **Interactive setup.** `antcrew setup` wizard generates a ready-to-use `agentteam.yaml` through a guided conversation — no YAML knowledge needed.
- **Integrations.** Push tickets to Jira, open PRs on GitHub, publish docs to Confluence, send reviews to Slack or Telegram (with per-agent recipients and free-text feedback buttons).

---

## Quick start

```python
from antcrew import DevTeam
from antcrew.models import SimulatedLLM   # no API key needed

team = DevTeam(model=SimulatedLLM())
result = team.run("Add a password reset flow to the auth module")

for artifact in result["code_artifacts"]:      # dict access works as before
    print(artifact.file_path, "—", artifact.description)

print(result.thread_id)   # LangGraph thread used
print(result.cost_usd)    # estimated API cost (0.0 with SimulatedLLM)
```

```python
# With real models
from antcrew import DevTeam
from antcrew.models import AnthropicModel, OllamaModel

team = DevTeam(model=AnthropicModel("claude-sonnet-4-6"))  # cloud
team = DevTeam(model=OllamaModel("llama3"))                # fully local

result = team.run("Build a REST API for user authentication")
print(result["prd"].title)           # PRD object
print(len(result["tickets"]))        # list[Ticket]
print(result.cost_usd)               # e.g. 0.43
```

---

## Teams

| Team | Agents / mode | Best for |
|---|---|---|
| `DevTeam` | BA → PM → BackendDev | Backend features, APIs |
| `FullStackTeam` | BA → PM → Backend → Frontend → QA → Reviewer → DevOps → DocWriter | Full-stack MVPs |
| `ResearchTeam` | Researcher → Writer | Technical research, blog posts |
| `ContentTeam` | Idea → Copywriter → Editor | Marketing content, docs |
| `FeatureTeam` | Single-feature pipeline (spec → code) | Isolated, fast feature delivery |
| `CustomTeam` | User-defined steps (code or YAML) | Fully custom pipelines |
| `Router` | Classifier → dispatches to any team/agent | Smart routing: simple vs. complex |
| `DirectAgent` | Single LLM call | Q&A, summaries, lightweight tasks |

```python
from antcrew import (
    DevTeam, FullStackTeam, ResearchTeam, ContentTeam,
    FeatureTeam, CustomTeam,
    Router, DirectAgent,
    LLMClassifier, RuleClassifier,
)
```

### Custom teams

Define any pipeline in code or YAML without subclassing:

```python
from antcrew import CustomTeam
from antcrew.agents import PMAgent, BackendDevAgent, QAAgent

team = CustomTeam(steps=[PMAgent, BackendDevAgent, QAAgent])
result = team.run("Build a rate-limiting middleware")
```

Or via YAML config with a `steps:` list and inline system prompts:

```yaml
team: custom
steps:
  - name: planner
    system_prompt: "You are a tech lead. Plan the implementation in bullet points."
  - name: coder
    system_prompt: "You are a senior Python developer. Implement the plan above."
```

### Template agents

Re-usable agent blueprints loaded from YAML — no Python required:

```python
from antcrew import load_template_agent, register_transform

# Load an agent from a YAML file
agent = load_template_agent("agents/security_reviewer.yaml")

# Register a custom post-process transform
@register_transform("extract_json")
def my_transform(text: str) -> str:
    import json, re
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    return m.group(1) if m else text
```

`agents/security_reviewer.yaml` example:

```yaml
name: security_reviewer
system_prompt: |
  You are a security expert. Review the code for OWASP Top 10 vulnerabilities.
  Return a JSON object with keys: severity (critical|high|medium|low), findings (list), verdict (pass|fail).
post_process: extract_json
```

Use `output_schema` to validate and coerce the LLM response to a known Pydantic model:

```yaml
name: prd_generator
system_prompt: |
  You are a product manager. Given the request, produce a PRD as JSON.
output_schema: PRD          # built-in: PRD, CodeArtifact, Ticket, CodeReview, etc.
output_key: prd             # stored as a PRD model instance, not a string
```

For custom models:

```yaml
output_schema: mypackage.models.FeatureSpec   # dotted import path
output_parse_retries: 2                        # retry LLM call on parse failure
```

The agent retries the LLM call up to `output_parse_retries` times when the response does not conform to the schema before raising.

### FeatureTeam + FeedbackLoop

Lightweight pipeline for a single, isolated feature — specification, implementation, and review in one step. Faster and cheaper than the full `DevTeam` when you don't need the BA/PM planning cycle.

```python
from antcrew import FeatureTeam
from antcrew.models import AnthropicModel

team = FeatureTeam(llm=AnthropicModel())
result = team.run("Add a /health endpoint to the FastAPI app")

print(result["feature_output"])   # implementation + brief explanation
```

**With FeedbackLoop** — execute-validate-retry until tests pass or the round budget runs out:

```python
from antcrew import FeatureTeam
from antcrew.models import AnthropicModel

team = FeatureTeam(
    llm=AnthropicModel(),
    project_dir="./src",
    max_feedback_rounds=3,
    validate_cmd=["pytest", "-x", "--tb=short"],
)
result = team.run("Add JWT authentication")
print(result.state["feedback_ok"])            # True if validation passed
print(result.state["feedback_rounds_used"])   # 1, 2, or 3
```

The loop: runs the agent → executes the validate command → feeds error output back into the agent's context → repeats until the command exits 0 or `max_feedback_rounds` is exhausted.

Use `FeedbackLoop` directly with any callable that accepts and returns a state dict:

```python
from antcrew.core.feedback import FeedbackRunner, FeedbackLoop
from antcrew.agents.feature_agent import FeatureAgent

runner = FeedbackRunner(["pytest", "-x", "--tb=short"], work_dir="./src")
loop   = FeedbackLoop(runner=runner, max_rounds=3)
agent  = FeatureAgent(AnthropicModel(), project_dir="./src")

final_state = loop.run(agent.run, {"request": "Add JWT auth"})
```

Via YAML:

```yaml
team: feature
model: claude
project_dir: ./src
feedback_rounds: 3
validate_cmd: ["pytest", "-x", "--tb=short"]
```

---

### DirectAgent

A single LLM call — no pipeline, no state graph. Perfect for Q&A, summarization, and any task that does not need multi-agent collaboration.

```python
from antcrew import DirectAgent
from antcrew.models import AnthropicModel

agent = DirectAgent(
    AnthropicModel(),
    system_prompt="You are a concise technical assistant.",
    output_key="answer",
)
result = agent.run("What is the difference between JWT and session tokens?")
print(result.state["answer"])
```

`DirectAgent.run()` returns a `RunResult` — same interface as all teams, so it composes anywhere a team is expected.

---

### Router — auto-routing

The `Router` classifies a request and dispatches it to the right team or agent. Avoids running a full multi-agent pipeline for simple requests that need only one LLM call.

**LLM-based classifier:**

```python
from antcrew import Router, DirectAgent, DevTeam, LLMClassifier
from antcrew.models import AnthropicModel

llm = AnthropicModel()
router = Router(
    classifier=LLMClassifier(llm, routes={
        "simple":  "Factual question or short explanation — no code generation needed",
        "complex": "Software development task, code generation, system design",
    }),
    routes={
        "simple":  DirectAgent(llm, system_prompt="Answer concisely."),
        "complex": DevTeam(model=llm),
    },
    default="complex",
)

result = router.run("What is JWT?")          # → simple, 1 LLM call
result = router.run("Build JWT auth module") # → complex, full pipeline
print(result.state["_route"])               # "simple" or "complex"
```

**Rule-based classifier (no LLM call, instant):**

```python
from antcrew import Router, RuleClassifier

router = Router(
    classifier=RuleClassifier(rules=[
        (r"\b(what|who|when|where|why|how|explain|define)\b", "simple"),
        (r"\b(build|create|implement|develop|generate|add)\b", "complex"),
    ], default="complex"),
    routes={"simple": DirectAgent(llm), "complex": DevTeam(model=llm)},
    default="complex",
)
```

**Use a cheaper model just for classification:**

```python
from antcrew.models import AnthropicModel, GroqModel

clf_llm = GroqModel("llama3-8b-8192")   # fast + cheap
main_llm = AnthropicModel()             # Claude for the actual work

router = Router(
    classifier=LLMClassifier(clf_llm, routes={...}),
    routes={"simple": DirectAgent(main_llm), "complex": DevTeam(model=main_llm)},
    default="complex",
)
```

**Via YAML — `team: auto` (quick setup):**

```yaml
team: auto
model: claude
simple_prompt: "You are a helpful assistant. Answer concisely."
complex_team: dev          # dev | fullstack | custom | feature
classifier_model: groq:llama3-8b-8192   # optional — separate model for routing
route_descriptions:
  simple: "Factual questions, quick explanations, no code needed"
  complex: "Code generation, software development, system design"
```

**Via YAML — `team: routed` (full control):**

```yaml
team: routed
model: claude
classifier: llm            # llm | rule
classifier_model: groq:llama3-8b-8192
default_route: complex
routes:
  simple:
    team: direct
    system_prompt: "Answer the question concisely."
  complex:
    team: dev
```

Rule-based routing (no LLM classification call):

```yaml
team: routed
model: claude
classifier: rule
rules:
  - ['\b(what|who|explain|define)\b', simple]
  - ['\b(build|create|implement)\b', complex]
default_route: complex
routes:
  simple:
    team: direct
  complex:
    team: fullstack
```

---

### Operators — pipeline state transforms

Operators are lightweight, reusable state transforms that run as steps in a `CustomTeam` pipeline. They rename keys, copy values, drop unused fields, merge strings, and more — with no LLM call.

```python
from antcrew import RenameOp, CopyOp, DropOp, SetOp, MergeOp, MapOp, CustomTeam

team = CustomTeam(
    steps=[
        agent_a,                          # produces "draft"
        RenameOp("draft", "content"),     # rename "draft" → "content"
        CopyOp("content", "backup"),      # copy "content" → "backup"
        SetOp("status", "in_review"),     # inject constant
        MergeOp(["title", "content"], "full_doc"),  # concatenate
        agent_b,                          # reads "content"
        DropOp("backup"),                 # remove temporary key
    ],
    llm=llm,
)
```

**Operator reference:**

| Class | Effect |
|---|---|
| `RenameOp(src, dst)` | Move `src` → `dst`, delete `src` |
| `CopyOp(src, dst)` | Copy `src` → `dst`, keep `src` |
| `DropOp(*keys)` | Remove one or more keys |
| `SetOp(key, value)` | Inject a constant value |
| `MergeOp(keys, dst)` | Concatenate multiple string keys |
| `MapOp(key, fn)` | Transform a value with a callable |

**In YAML:**

```yaml
team: custom
steps:
  - name: planner
    system_prompt: "Plan the implementation."
    output_key: plan

  - operator: rename
    from: plan
    to: spec

  - operator: set
    key: approved
    value: true

  - name: coder
    system_prompt: "Implement the spec."
    output_key: code
```

All operators implement `run(state) -> dict` — they compose with `CustomTeam.steps` exactly like agents.

---

### ArtifactContract — type-safe state access

Built-in agents use `ArtifactContract` to read typed artifacts from state. Use it in your own custom agents for safe, coercible reads.

```python
from antcrew.core.artifacts import ArtifactContract, ContractError, PRD, CodeArtifact
from antcrew.core.artifacts import coerce_model, coerce_list

# Single-model read — raises ContractError if missing or wrong type
_PRD_CONTRACT = ArtifactContract("prd", PRD)

class MyAgent:
    def run(self, state: dict) -> dict:
        try:
            prd = _PRD_CONTRACT.extract(state)   # → PRD instance, always
        except ContractError as exc:
            return {"errors": [str(exc)]}
        # use prd.title, prd.summary, etc.

# List coercion — converts list of dicts or mixed instances
artifacts = coerce_list(state, "code_artifacts", CodeArtifact)  # → list[CodeArtifact]

# Single coercion
review = coerce_model(state["review"], CodeReview)   # dict or instance → CodeReview
```

---

### validate_agent_dag — DAG validation

Validate that each agent's `consumes` keys are produced by prior agents before running the pipeline:

```python
from antcrew import validate_agent_dag
from antcrew.agents import PMAgent, BackendDevAgent, QAAgent
from antcrew.models import SimulatedLLM

llm = SimulatedLLM()
agents = [PMAgent(llm), BackendDevAgent(llm), QAAgent(llm)]

# strict=True (default): raises ValueError on the first violation
validate_agent_dag(agents, initial_keys={"request", "prd"})

# strict=False: returns list of violation strings without raising
violations = validate_agent_dag(agents, initial_keys={"request"}, strict=False)
for v in violations:
    print(v)
```

Enable automatic validation when building a `CustomTeam`:

```python
team = CustomTeam(
    steps=[agent_a, agent_b, agent_c],
    llm=llm,
    validate_dag=True,   # raises at construction time if DAG is invalid
)
```

Inspect the DAG from the CLI:

```bash
antcrew dag agentteam.yaml           # validate + display dependency table
antcrew dag agentteam.yaml --no-strict   # print violations but exit 0
```

---

## Models

| Model string (YAML / CLI) | Python class | Notes |
|---|---|---|
| `claude` / `claude-sonnet-4-6` | `AnthropicModel` | Default |
| `gpt-4o` / `gpt-4o-mini` | `OpenAIModel` | Any OpenAI model |
| `azure:gpt-4o` | `AzureOpenAIModel` | Azure OpenAI with deployment name |
| `gemini` / `gemini-1.5-pro` | `GeminiModel` | Google Gemini via REST |
| `groq:llama3-70b-8192` | `GroqModel` | Groq ultra-fast inference |
| `ollama:llama3` | `OllamaModel` | Local via Ollama |
| `simulated` | `SimulatedLLM` | Fixtures, CI, demos — no API |

### Azure OpenAI

```python
from antcrew.models import AzureOpenAIModel

team = DevTeam(model=AzureOpenAIModel(
    deployment="gpt-4o",
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_version="2024-02-01",
))
```

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
antcrew setup        Interactive wizard — generate agentteam.yaml from scratch
antcrew run          Run a pipeline autonomously
antcrew interactive  Run with human-in-the-loop review after every agent
antcrew describe     Show pipeline agents, data contracts, historical cost (no API key)
antcrew agents       List all built-in agent types and their role descriptions
antcrew replay       Resume a pipeline from its last SqliteSaver checkpoint
antcrew trace        Inspect a TraceLog SQLite file — list runs or show per-agent detail
antcrew history      Aggregated cost and usage history across all runs
antcrew project      Manage persistent project sessions
antcrew eval         Run evaluation cases and score results
antcrew benchmark    Run a batch of requests and compare results across teams/models
antcrew watch        Watch a directory and auto-run the pipeline on changes
antcrew diff         Compare two saved state files side by side
antcrew test         Run generated tests from a saved state
antcrew export       Export artifacts from a saved state to a directory
antcrew graph        Render a pipeline flow as an ASCII graph or Mermaid diagram
antcrew validate     Validate a saved state or config file
antcrew serve        Start the REST API + web dashboard
antcrew show         Display a previously saved state file
antcrew extract      Extract specific artifacts from a saved state to disk
antcrew init         Generate a starter agentteam.yaml + main.py
antcrew flow         Validate and inspect flow config files
antcrew publish      Push artifacts to GitHub (PR), Confluence, or export to a directory
antcrew write-back   Apply generated artifacts back to project files (brownfield)
antcrew scan         Preview what CodebaseScannerAgent sees in a directory (no LLM needed)
antcrew dag          Validate and display the agent dependency graph for a config file
```

### `antcrew setup`

Conversational wizard for non-developers. Asks questions and generates a ready-to-use `agentteam.yaml`:

```bash
antcrew setup                              # interactive (output: ./agentteam.yaml)
antcrew setup --name auth-service          # skip the name question
antcrew setup --output ./myproject/        # write to a specific directory
antcrew setup --filename team.yaml         # custom filename
```

```
? What do you want to build? > A booking app for my restaurant
? What type of project?
  > 1  Software (API / backend / web app)
    2  Full-stack (frontend + backend)
    3  Research (analysis, reports)
    4  Content (blog, marketing)
? Which AI model?
  > 1  Claude  → ANTHROPIC_API_KEY
    2  GPT-4o  → OPENAI_API_KEY
    3  Gemini  → GOOGLE_API_KEY
    4  Ollama  → local, no API key
    5  Simulated → no API key (testing)
? Save persistent sessions between runs? [y/N]
? Enable LLM cache? [y/N]
? Test runner sandbox?
  > 1  None
    2  Local (pytest / npm test)
    3  Docker

Generated agentteam.yaml ✅
Run with: antcrew run "A booking app for my restaurant" --config agentteam.yaml
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

# Persistent thread state — resume from where you left off (pip install antcrew[sqlite])
antcrew run "Build JWT auth" --team dev --thread sprint-1 --checkpointer ~/.antcrew/threads.db
antcrew run "Add OAuth"      --team dev --thread sprint-1 --checkpointer ~/.antcrew/threads.db

# Full observability stack — tracing + cost guard + checkpointing
antcrew run "Build JWT auth" --team dev --thread sprint-1 \
  --checkpointer ~/.antcrew/threads.db \
  --trace ~/.antcrew/trace.db \
  --max-cost 2.00

# If the run was interrupted, replay from last checkpoint (zero re-specification)
antcrew replay sprint-1 \
  --checkpointer ~/.antcrew/threads.db \
  --trace ~/.antcrew/trace.db

# Inspect what ran
antcrew trace ~/.antcrew/trace.db
antcrew trace ~/.antcrew/trace.db --thread sprint-1

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
team: dev                        # dev | fullstack | research | content | custom | feature | auto | routed
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

Every artifact is tagged with the run number that created it:

```python
project.run("Build JWT auth")    # run 1
project.run("Add OAuth")         # run 2

for a in project.state["code_artifacts"]:
    print(a.file_path, "→ run", a.created_at_run)
# auth/jwt.py      → run 1
# auth/oauth.py    → run 2
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

## Brownfield write-back

Use `CodebaseScannerAgent` to give the pipeline context about your existing codebase,
then write the generated artifacts back to the real files instead of `./generated/`.

```bash
# Step 1 — run the pipeline and save state
antcrew run "Add rate limiting to the auth API" \
  --team fullstack --config agentteam.yaml --save run.json

# Step 2 — preview what would change (no writes)
antcrew write-back run.json --project-root ~/myproject --dry-run

# Step 3 — apply: new files are created, existing files show a diff and ask for confirmation
antcrew write-back run.json --project-root ~/myproject

# Or skip confirmation entirely (e.g. in a script after reviewing the dry run)
antcrew write-back run.json --project-root ~/myproject --yes
```

Or write back immediately after a run:

```bash
antcrew run "Add rate limiting" --team fullstack \
  --write-back ~/myproject --write-back-yes
```

`write-back` resolves each artifact's `file_path` relative to `project-root`:
- **New files** are created (including parent directories).
- **Existing files** are shown as a unified diff and require confirmation (unless `--yes`).
- Files where the content is **identical** are silently skipped.

Point the scanner at your repo so agents understand what already exists:

```bash
# Preview what the scanner sees (no LLM, no API key)
antcrew scan ./src
antcrew scan backend:./src frontend:./client infra:./terraform

# Run with full LLM analysis
antcrew scan ./src --model claude
```

Pass directories to the pipeline directly from CLI (no YAML needed):

```bash
# Single directory
antcrew run "Add rate limiting" --team fullstack --project-dir ./src

# Multiple components
antcrew run "Migrate auth to OAuth2" --team fullstack \
  --project-dir backend:./src \
  --project-dir frontend:./client \
  --save run.json

antcrew write-back run.json --dry-run   # auto-detects root from project_dirs
antcrew write-back run.json --yes        # apply
```

Or via YAML for repeatable workflows:

```yaml
# agentteam.yaml
team: fullstack
model: claude
project_dirs:
  backend: ./src
  frontend: ./client
  infra: ./terraform
```

```bash
antcrew run "Migrate auth from JWT to OAuth2" --config agentteam.yaml --save run.json
antcrew write-back run.json --dry-run  # review
antcrew write-back run.json --yes       # apply
```

### Two-step workflow: scan once, run many times

Scanning with an LLM is the most expensive part of a brownfield run. Save the result to a
file and reuse it across multiple `run` calls:

```bash
# Step 1 — scan once and persist the analysis (one LLM call)
antcrew scan ./src --model claude --output ctx.json

# Step 2 — run as many times as you like (no re-scan, no scanner LLM call)
antcrew run "Add billing module"  --team fullstack --context ctx.json
antcrew run "Add email alerts"    --team fullstack --context ctx.json
antcrew run "Migrate to Postgres" --team fullstack --context ctx.json
```

Preview the context that will be injected at run time:

```bash
antcrew describe --team fullstack --context ctx.json
```

Filter the file tree to recently modified files (useful for large repos):

```bash
antcrew scan ./src --since 7          # files touched in the last 7 days
antcrew scan ./src --since 7 --model claude --output ctx.json
```

### Semantic code search with RepoIndex

Attach a vector index of the codebase so agents can perform semantic search over
the source tree during generation (requires `pip install antcrew[memory]`):

```bash
antcrew run "Refactor auth" --team fullstack --repo-index ./src
```

### AST Symbol Index

`SymbolIndex` parses Python files via `ast` (no LLM, no network) and builds an
exact index of every public function, method, and class — including signatures,
argument names, return types, and source locations.

```python
from antcrew.core.symbol_index import SymbolIndex

idx = SymbolIndex.build(["src/", "lib/"])
print(idx.summary())
# → "42 functions, 8 classes across 15 files"

# Exact lookup
hits = idx.query_function("hash_password")
# → [FunctionSymbol(name="hash_password", signature="(plain: str) -> str", ...)]

# Context snippet for injection into an agent prompt
print(idx.context_for(["auth", "User"]))
```

When `DevTeam(repo_path="./src")` is used, `SymbolIndex` is built automatically
alongside `RepoIndex` and attached to all agents.  `BackendDevAgent` injects the
symbol context before generating code, so it knows what names to import.

---

### Project Knowledge Base

`ProjectKB` stores structured knowledge that persists across pipeline runs: API
endpoints, data models, service classes, and dependencies.  Unlike free-text memory
it can be queried programmatically.

```python
from antcrew.core.project_kb import ProjectKB

kb = ProjectKB.load("./.antcrew/kb.json")
print(kb.summary())
# → "12 endpoints, 5 models, 3 services, 8 dependencies"
print(kb.context_for_agent("backend_dev"))
# → formatted block injected into BackendDevAgent context
```

**Python:**

```python
team = DevTeam(model=llm, project_kb_path="./.antcrew/kb.json")
result = team.run("Add order history endpoint")
# KB is loaded, injected into context, then updated and saved after each run
```

**YAML:**

```yaml
team: dev
model: claude
project_kb_path: ./.antcrew/kb.json
```

---

### CoherenceAgent — cross-file consistency pass

`CoherenceAgent` receives all generated files in a single LLM call and fixes:
- Broken imports (name doesn't exist in the referenced module)
- Signature mismatches (caller args don't match definition)
- Type inconsistencies across files

```python
team = DevTeam(model=llm, enable_coherence=True)
result = team.run("Build auth module with user service")
print(result.state["coherence_issues"])  # list of corrected file paths
```

**YAML:**

```yaml
team: dev
model: claude
enable_coherence: true
```

The pass runs between pipeline completion and the test runner, so tests see the
already-corrected code.  Use `CoherenceAgent` standalone in a `CustomTeam` too.

---

### Lint pre-pass in FeedbackLoop

When `feedback_rounds > 0`, add a fast static check that runs before the test
suite each round.  Lint failures (import errors, type errors) are fed to the
agent without consuming a full sandbox run:

```python
team = DevTeam(
    model=llm,
    runner=LocalRunner(test_cmd=["pytest", "-x"]),
    feedback_rounds=3,
    lint_cmd=["ruff", "check", "."],   # or ["mypy", "src/"]
    work_dir="./src",
)
```

**YAML:**

```yaml
team: dev
model: claude
feedback_rounds: 3
lint_cmd: ["ruff", "check", "."]
work_dir: ./src
runner:
  type: local
  test_cmd: ["pytest", "-x", "--tb=short"]
```

State key written: `lint_ok` (bool) after each lint run.

---

### QAAgent — code-first test generation

`QAAgent` now extracts the public API of each source file via `ast` before generating
tests.  The exact function and class names that exist in the code are prepended to the
LLM context, preventing tests that import non-existent names.

The improvement is automatic — no configuration needed.  To use the symbol extractor
directly:

```python
from antcrew.agents.qa import _extract_symbols_context

ctx = _extract_symbols_context(source_code, "auth.py")
# → "Public symbols in this file (import exactly these names):\n  def login(user, password)\n  class AuthService\n..."
```

---

### Minimal pipelines — task-type routing

Instead of running 8 agents for every request, `MinimalPipeline` classifies the task
and selects the narrowest pipeline:

| Task type | Agents used                     |
|-----------|--------------------------------|
| `fix`     | BackendDevAgent + QAAgent       |
| `refactor`| BackendDevAgent + ReviewerAgent |
| `feature` | BusinessAnalyst + PM + BackendDev (default) |
| `test`    | QAAgent only                   |
| `docs`    | DocWriterAgent only            |

```python
from antcrew import MinimalPipeline, TaskType

# Auto-classify from request text (rule-based, no LLM call):
pipeline = MinimalPipeline(model=llm)
result = pipeline.run("Fix the failing test in auth.py")
print(result.state["_task_type"])   # "fix"

# Force a task type:
pipeline = MinimalPipeline(model=llm, task_type=TaskType.REFACTOR)

# LLM classifier (more accurate, costs one extra LLM call):
pipeline = MinimalPipeline(model=llm, use_llm_classifier=True)
```

**YAML:**

```yaml
team: minimal
model: claude
task_type: auto          # auto | fix | refactor | feature | test | docs
use_llm_classifier: true # optional, default false
```

`MinimalPipeline` accepts all `DevTeam` kwargs: `feedback_rounds`, `lint_cmd`,
`enable_coherence`, `project_kb_path`, etc.

---

### Sprint planning

Divide a product backlog into fixed-size sprints without an LLM call:

```bash
# backlog.json — plain array or {"tickets": [...]} from a run
antcrew sprint backlog.json              # 4 tickets per sprint (default)
antcrew sprint backlog.json --size 6     # custom sprint size
antcrew sprint backlog.json --json       # machine-readable output
antcrew sprint backlog.json --output sprints.json

# Pipe directly from an antcrew run result
antcrew run "Build e-commerce platform" --team fullstack --json | antcrew sprint --json
```

`backlog.json` can be:
- A plain array: `["Add auth", "Billing", "CI pipeline"]`
- An array of objects: `[{"title": "Add auth", "priority": "high"}, ...]`
- A run-state dict: `{"tickets": ["Add auth", ...]}` (output of `antcrew run --json`)

---

### LLM cost tracking

Record spending across runs with `--trace` and inspect it later:

```bash
antcrew run "Add billing" --team fullstack --trace ~/.antcrew/trace.db

# Summary: total spend, avg per run, per-team breakdown
antcrew cost

# Filter by team or date range
antcrew cost --team fullstack --since 7
antcrew cost --json | jq '.total_cost_usd'
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

## Agent Presets

Tune agent behaviour without touching prompts:

```python
from antcrew import DevTeam, CONCISE, STRICT, VERBOSE, CAREFUL
from antcrew.presets import AgentPreset, get_preset

team = DevTeam(model=llm, preset=CONCISE)    # shorter outputs, less explanation
team = DevTeam(model=llm, preset=STRICT)     # stricter validation, more rejections
team = DevTeam(model=llm, preset=VERBOSE)    # detailed reasoning, longer outputs
team = DevTeam(model=llm, preset=CAREFUL)    # cautious, asks for confirmation

# Or build your own
my_preset = AgentPreset(
    temperature=0.2,
    max_tokens=2048,
    extra_instructions="Always respond in Spanish.",
)
team = DevTeam(model=llm, preset=my_preset)
```

---

## Agent Tools

Give agents access to real-world capabilities:

```python
from antcrew import DevTeam
from antcrew.core.tools import WebSearchTool, CodeExecutorTool, ReadFileTool, BaseTool, ToolResult

team = DevTeam(
    model=llm,
    tools=[
        WebSearchTool(),          # DuckDuckGo search (no API key)
        CodeExecutorTool(),       # Execute Python snippets in a sandbox
        ReadFileTool(allowed_dirs=["./src"]),  # Read files from allowed paths
    ],
)
```

Define custom tools:

```python
from antcrew.core.tools import BaseTool, ToolResult

class DatabaseQueryTool(BaseTool):
    name = "query_db"
    description = "Run a read-only SQL query and return results as JSON."

    def run(self, query: str) -> ToolResult:
        rows = my_db.execute(query).fetchall()
        return ToolResult(output=str(rows))

team = DevTeam(model=llm, tools=[DatabaseQueryTool()])
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

### Slack

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

### Telegram

Each agent can have its own bot and its own reviewer. Requires `pip install antcrew[telegram]`.

```python
from antcrew import TelegramChannel, AgentBotConfig
from antcrew.agents import PMAgent, BackendDevAgent

# Single bot, one reviewer
channel = TelegramChannel(token=os.environ["BOT_TOKEN"], chat_id=REVIEWER_CHAT_ID)

# Single bot, notify multiple recipients (HITL goes to first, all get status)
channel = TelegramChannel(
    token=os.environ["BOT_TOKEN"],
    notify=[MARIA_CHAT_ID, JUAN_CHAT_ID],
)

# Per-agent: each agent notifies a different person
pm = PMAgent(
    channel=TelegramChannel(token=TOKEN, notify=[MARIA_CHAT_ID]),
    approval_required=True,
)
dev = BackendDevAgent(
    channel=TelegramChannel(token=TOKEN, notify=[JUAN_CHAT_ID]),
    approval_required=False,
)
```

HITL review buttons sent after each artifact:

```
🤖 PMAgent has completed the PRD

[✅ Aprobar]  [✏️ Sugerir cambios]  [❌ Rechazar]
```

If "Sugerir cambios" is pressed, the bot asks for free-text feedback and passes it to `agent.refine()` — the agent revises its output in place and re-sends for review.

---

## Async teams

Every built-in team has an async wrapper that delegates `.run()` to `asyncio.to_thread`, keeping the event loop unblocked.  The sync `run_sync()` method is always available as a convenience.

| Sync class | Async wrapper |
|---|---|
| `DevTeam` | `AsyncDevTeam` |
| `FullStackTeam` | `AsyncFullStackTeam` |
| `ResearchTeam` | `AsyncResearchTeam` |
| `ContentTeam` | `AsyncContentTeam` |
| `CustomTeam` | `AsyncCustomTeam` |
| `FeatureTeam` | `AsyncFeatureTeam` |
| `Router` | `AsyncRouter` |

```python
import asyncio
from antcrew import AsyncDevTeam, AsyncCustomTeam, AsyncRouter
from antcrew.models import AnthropicModel

llm = AnthropicModel()

# Async usage
async def main():
    team = AsyncDevTeam(model=llm)
    result = await team.run("Build JWT auth module")
    print(result["code_artifacts"])

# Run two teams concurrently
async def run_parallel():
    dev_team = AsyncDevTeam(model=llm)
    research_team = AsyncResearchTeam(model=llm)
    dev_result, research_result = await asyncio.gather(
        dev_team.run("Build auth API"),
        research_team.run("Research JWT best practices"),
    )

asyncio.run(main())
```

`AsyncCustomTeam`, `AsyncFeatureTeam`, and `AsyncRouter` use composition (not inheritance) so they accept the same constructor arguments as their sync counterparts:

```python
team = AsyncCustomTeam(
    steps=[agent_a, RenameOp("draft", "content"), agent_b],
    llm=llm,
)
result = await team.run("Build the feature")

router = AsyncRouter(
    classifier=RuleClassifier(rules=[...], default="complex"),
    routes={"simple": DirectAgent(llm), "complex": DevTeam(model=llm)},
    default="complex",
)
result = await router.run("What is REST?")
```

---

## Testing utilities

### `SequencedLLM`

A test-only LLM that returns pre-defined responses in order. No API calls, fully deterministic.

```python
from antcrew.testing import SequencedLLM

llm = SequencedLLM([
    "Step 1: analyse requirements",
    '{"title": "Auth PRD", "summary": "JWT authentication"}',
    "def authenticate(token): ...",
])

team = CustomTeam(steps=[agent_a, agent_b, agent_c], llm=llm)
result = team.run("Build auth")
# agent_a gets "Step 1: ...", agent_b gets the JSON, agent_c gets the code
```

Useful properties:

```python
llm.call_count        # number of times the LLM was called
llm.last_max_tokens   # max_tokens from the last call
llm.get_usage_summary()  # same format as real LLMs — works with cost tracking
```

`SequencedLLM` is also exported directly from `antcrew`:

```python
from antcrew import SequencedLLM
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

The full suite uses `SimulatedLLM` — no API keys required. ~1700 tests, no live network calls.

---

## License

[MIT](LICENSE)
