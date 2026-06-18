"""
03 — Project sessions + persistent LLM cache

Each run builds on the previous one:
  run 1: PRD + tickets + code
  run 2: agents see run-1 context, add more tickets and code files
  run 3: same — total grows

FileLLMCache ensures identical prompts are served from disk instead of
making a new API call, making repeated runs much cheaper.

Requires: export ANTHROPIC_API_KEY=sk-ant-...
Or swap AnthropicModel for SimulatedLLM to test without an API key.
"""
from pathlib import Path

from antcrew import DevTeam, FileLLMCache, Project
from antcrew.models import AnthropicModel

PROJECT_FILE = Path("auth-service.json")
CACHE_FILE   = Path(".antcrew_cache.db")

# Attach a persistent LLM cache — responses survive process restarts
llm = AnthropicModel()
llm.with_cache(FileLLMCache(CACHE_FILE))

team    = DevTeam(model=llm)
project = Project(team, name="auth-service", path=PROJECT_FILE)

# ── Run 1 ─────────────────────────────────────────────────────────────────────
print("=== Run 1: Build JWT auth ===")
project.run("Build JWT authentication with access and refresh tokens")
print(f"  Tickets so far:    {len(project.state.get('tickets', []))}")
print(f"  Code files so far: {len(project.state.get('code_artifacts', []))}")

# ── Run 2 ─────────────────────────────────────────────────────────────────────
print("\n=== Run 2: Add OAuth ===")
project.run("Add OAuth2 login with Google and GitHub providers")
print(f"  Tickets so far:    {len(project.state.get('tickets', []))}")
print(f"  Code files so far: {len(project.state.get('code_artifacts', []))}")

# ── Run 3 ─────────────────────────────────────────────────────────────────────
print("\n=== Run 3: Add rate limiting ===")
project.run("Add rate limiting and brute-force protection to the auth endpoints")
print(f"  Tickets total:    {len(project.state.get('tickets', []))}")
print(f"  Code files total: {len(project.state.get('code_artifacts', []))}")

print(f"\nProject saved to: {PROJECT_FILE}")
print(f"Cache saved to:   {CACHE_FILE}")
print("\nFull summary:")
print(project.summary())
