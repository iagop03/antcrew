"""Template strings used by `antcrew init` to scaffold new projects."""
from __future__ import annotations

_YAML_DEV = """\
# AntCrew — Dev Team configuration
# Default pipeline: BusinessAnalyst → PM → BackendDev
team: dev
model: claude          # claude | gpt-4o | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides (Level 2)
# agents:
#   backend_dev:
#     model: ollama:llama3
#     approval_required: true
#     response_options: [approve, reject]
#   devops:
#     model: claude
#     approval_required: true

# Optional: HITL channel (uncomment one)
# channel:
#   type: console

# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# channel:
#   type: telegram
#   token: ${BOT_TOKEN}
#   chat_id: ${CHAT_ID}

# Optional: extended pipeline (Level 3)
# flow:
#   - [business_analyst, pm]
#   - [pm, backend_dev]
#   - [pm, frontend_dev]     # parallel frontend track
#   - [backend_dev, qa]
#   - [frontend_dev, qa]
#   - [qa, reviewer]
#   - [backend_dev, devops]  # add DevOps step

# Optional: persistent LLM response cache (avoids repeated API calls)
# cache: ~/.antcrew/cache.db

# Optional: persistent project sessions (state accumulates across runs)
# project: ./my-project.json
"""

_MAIN_DEV = '''\
import os
from antcrew import DevTeam, save_state
from antcrew.models.anthropic_model import AnthropicModel

team = DevTeam(model=AnthropicModel())
state = team.run("Build a REST API with JWT authentication")

# ── Print code artifacts ──────────────────────────────────────────────────────
if state.get("code_artifacts"):
    for artifact in state["code_artifacts"]:
        print(f"\\n--- {artifact.file_path} ---")
        print(artifact.content)

# ── Print DevOps artifacts ────────────────────────────────────────────────────
if state.get("devops_artifacts"):
    for artifact in state["devops_artifacts"]:
        print(f"\\n--- {artifact.file_path} ({artifact.language}) ---")
        print(artifact.content)

# ── Save state to JSON (optional) ────────────────────────────────────────────
save_state(state, "output/run.json")
print("\\nState saved to output/run.json")

# ── Sync tickets to Jira (optional) ──────────────────────────────────────────
# from antcrew import JiraIntegration
# jira = JiraIntegration(
#     url=os.environ["JIRA_URL"],
#     email=os.environ["JIRA_EMAIL"],
#     api_token=os.environ["JIRA_API_TOKEN"],
#     project_key=os.environ.get("JIRA_PROJECT_KEY", "DEV"),
# )
# pairs = jira.sync_tickets(state["tickets"] or [])
# for ticket, key in pairs:
#     print(f"  {ticket.id} → {key}")

# ── Open GitHub PR with code artifacts (optional) ────────────────────────────
# from antcrew import GitHubIntegration
# gh = GitHubIntegration(
#     token=os.environ["GITHUB_TOKEN"],
#     repo=os.environ["GITHUB_REPO"],   # "your-org/your-repo"
# )
# pr_url = gh.create_pr(state)
# print(f"PR: {pr_url}")
'''

_YAML_FULLSTACK = """\
# AntCrew — Full-Stack Team configuration
# Pipeline: BA → PM → BackendDev → FrontendDev → QA → Reviewer → DevOps → DocWriter
team: fullstack
model: claude          # claude | gpt-4o | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides
# agents:
#   pm:
#     model: claude-sonnet-4-6  # stronger model for product thinking
#     approval_required: true   # pause and review tickets before coding starts
#   frontend_dev:
#     model: gpt-4o             # GPT-4o for frontend code
#   reviewer:
#     approval_required: true   # mandatory human review before DevOps runs
#   devops:
#     model: ollama:llama3      # local model for CI/CD templates

# Optional: HITL channel (uncomment one)
# channel:
#   type: console

# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# Optional: custom pipeline (skip or reorder steps)
# flow:
#   - [business_analyst, pm]
#   - [pm, backend_dev]
#   - [pm, frontend_dev]          # run frontend in parallel with backend
#   - [backend_dev, qa]
#   - [frontend_dev, qa]
#   - [qa, reviewer, no_critical_bugs]
#   - [qa, backend_dev, has_critical_bugs]
#   - [reviewer, devops]
#   - [devops, doc_writer]
"""

_MAIN_FULLSTACK = '''\
import os
from antcrew import FullStackTeam, save_state
from antcrew.models import AnthropicModel

team = FullStackTeam(model=AnthropicModel())
state = team.run("Build a task management app with a REST API and React frontend")

# ── Backend code ─────────────────────────────────────────────────────────────
if state.get("code_artifacts"):
    print(f"\\nBackend: {len(state[\'code_artifacts\'])} files")
    for a in state["code_artifacts"]:
        print(f"  {a.file_path}")

# ── Frontend code ─────────────────────────────────────────────────────────────
# (frontend artifacts share the code_artifacts key)

# ── DevOps artifacts ──────────────────────────────────────────────────────────
if state.get("devops_artifacts"):
    print(f"\\nDevOps: {len(state[\'devops_artifacts\'])} files")
    for a in state["devops_artifacts"]:
        print(f"  {a.file_path}  ({a.language})")

# ── Documentation ─────────────────────────────────────────────────────────────
if state.get("doc_artifacts"):
    print(f"\\nDocs: {len(state[\'doc_artifacts\'])} files")
    for a in state["doc_artifacts"]:
        print(f"  {a.file_path}  ({a.doc_type})")

# ── Code review verdict ───────────────────────────────────────────────────────
if state.get("review"):
    print(f"\\nCode review: {state[\'review\'].verdict.upper()}")
    print(f"  {state[\'review\'].summary}")

# ── Save state ────────────────────────────────────────────────────────────────
save_state(state, "output/fullstack_run.json")
print("\\nState saved to output/fullstack_run.json")

# ── Publish docs to Confluence (optional) ─────────────────────────────────────
# from antcrew import ConfluenceIntegration
# confluence = ConfluenceIntegration(
#     url=os.environ["CONFLUENCE_URL"],
#     email=os.environ["CONFLUENCE_EMAIL"],
#     api_token=os.environ["CONFLUENCE_TOKEN"],
# )
# confluence.publish_docs(state, space_key="ENG")

# ── Open a GitHub PR (optional) ───────────────────────────────────────────────
# from antcrew import GitHubIntegration
# gh = GitHubIntegration(token=os.environ["GITHUB_TOKEN"], repo="myorg/myapp")
# pr_url = gh.create_pr(state)
# print(f"PR: {pr_url}")
'''

_YAML_RESEARCH = """\
# AntCrew — Research Team configuration
# Pipeline: ResearcherAgent → CopywriterAgent (writer)
team: research
model: claude          # claude | gpt-4o | gemini | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides
# agents:
#   researcher:
#     model: ollama:llama3      # run researcher locally
#     approval_required: true   # pause for review after research
#   writer:
#     model: gpt-4o             # use GPT-4o for writing

# Optional: Console HITL channel (pauses in terminal for review)
# channel:
#   type: console

# Optional: Slack HITL channel
# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# Optional: custom flow (Level 3)
# Add an editor step after writing:
# flow:
#   - [researcher, writer]
#   - [writer, editor]
"""

_MAIN_RESEARCH = '''\
from antcrew import ResearchTeam
from antcrew.models import AnthropicModel

team = ResearchTeam(model=AnthropicModel())
state = team.run("What are the main challenges in deploying LLMs at scale?")

doc = state.get("research_document")
if doc:
    print(f"\\n=== {doc.title} ===")
    print(f"Topic: {doc.topic}")
    print()
    for finding in doc.key_findings:
        print(f"• {finding}")
    print()
    for section in doc.sections:
        print(f"## {section.heading}")
        print(section.content)
        print()

piece = state.get("content_piece")
if piece and piece.body:
    print(f"\\n--- Written Report ({piece.word_count or \'?\'} words) ---")
    print(piece.body)
'''

_YAML_CONTENT = """\
# AntCrew — Content Team configuration
# Pipeline: IdeaAgent → CopywriterAgent → EditorAgent
team: content
model: claude          # claude | gpt-4o | gemini | ollama:<name> | groq:<name> | simulated

# Optional: per-agent model / approval overrides
# agents:
#   idea:
#     model: claude
#     approval_required: true   # review the brief before writing
#   copywriter:
#     model: gpt-4o             # use GPT-4o for the body
#     approval_required: true   # review the draft before editing
#   editor:
#     approval_required: true   # review the final edit

# Optional: Console HITL channel (pauses in terminal for review)
# channel:
#   type: console

# Optional: Slack HITL channel
# channel:
#   type: slack
#   bot_token: ${SLACK_BOT_TOKEN}
#   app_token: ${SLACK_APP_TOKEN}
#   channel_id: ${SLACK_CHANNEL_ID}

# Optional: skip the editor (two-stage pipeline)
# flow:
#   - [idea, copywriter]
"""

_MAIN_CONTENT = '''\
from antcrew import ContentTeam
from antcrew.models import AnthropicModel

team = ContentTeam(model=AnthropicModel())
state = team.run("Write a blog post about multi-agent AI frameworks for software engineers")

piece = state.get("content_piece")
if piece:
    print(f"\\n=== {piece.title} ===")
    print(f"Audience : {piece.target_audience}")
    print(f"Tone     : {piece.tone}")
    print(f"Words    : {piece.word_count or \'?\'}")
    print()
    print(piece.body)
'''

_YAML_CUSTOM = """\
# AntCrew — Custom Team configuration
# Define your own pipeline as a sequence of YAML-configured agents.
# No Python required: each step is a TemplateAgent run in order.
team: custom
model: claude          # claude | gpt-4o | gemini | ollama:<name> | groq:<name> | simulated

steps:
  - name: planner
    system_prompt: |
      You are an expert project planner.
      Break the following task into a clear, numbered step-by-step plan.
      Output only the plan — no preamble, no explanations.
    output_key: plan

  - name: executor
    system_prompt: |
      You are a senior software engineer.
      Implement the following plan and return production-ready code with brief comments.

      Plan:
      {plan}
    input_key: plan
    output_key: code

  - name: reviewer
    system_prompt: |
      You are a code reviewer.
      Review the code below for correctness, security, and clarity.
      Provide concise, actionable feedback.

      Code:
      {code}
    input_key: code
    output_key: review

# Optional: per-step model override — add 'model:' inside the step block
# (not yet supported; use a Python Pipeline of CustomTeams for mixed models)

# Optional: LLM response cache
# cache: ~/.antcrew/cache.db

# Optional: per-run cost limit
# max_cost_usd: 2.00
"""

_MAIN_CUSTOM = '''\
from antcrew import CustomTeam
from antcrew.models import AnthropicModel

team = CustomTeam(
    steps=[
        {
            "name": "planner",
            "system_prompt": "Break the task into a numbered step-by-step plan.",
            "output_key": "plan",
        },
        {
            "name": "executor",
            "system_prompt": "Implement the following plan:\\n\\n{plan}",
            "input_key": "plan",
            "output_key": "code",
        },
        {
            "name": "reviewer",
            "system_prompt": "Review this code for correctness and security:\\n\\n{code}",
            "input_key": "code",
            "output_key": "review",
        },
    ],
    llm=AnthropicModel(),
)

result = team.run("Build a JWT authentication module")

print("\\n=== Plan ===")
print(result.get("plan", ""))
print("\\n=== Code ===")
print(result.get("code", ""))
print("\\n=== Review ===")
print(result.get("review", ""))
'''
