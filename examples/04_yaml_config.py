"""
04 — Load team from a YAML / JSON config file

The config file at examples/agentteam.yaml configures the model, optional
cache, optional project persistence, and per-agent overrides — no Python
needed for day-to-day use.

Run:
    python examples/04_yaml_config.py
"""
from pathlib import Path

from antcrew.config import load_context

CONFIG = Path(__file__).parent / "agentteam.yaml"

ctx = load_context(CONFIG)
team    = ctx.team
project = ctx.project   # None if no `project:` key in YAML

if project:
    print(f"Continuing project '{project.name}' (run #{len(project.history) + 1})")
    state = project.run("Add rate limiting to the API endpoints")
else:
    print("Running one-shot pipeline from config")
    state = team.run("Build a REST API with JWT authentication")

if state.get("tickets"):
    print(f"\nTickets: {len(state['tickets'])}")
    for t in state["tickets"]:
        print(f"  [{t.priority.value}] {t.title}")

if state.get("code_artifacts"):
    print(f"\nCode files: {len(state['code_artifacts'])}")
    for a in state["code_artifacts"]:
        print(f"  {a.file_path}")
