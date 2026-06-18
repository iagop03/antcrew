"""
01 — Quick start (no API key required)

Uses SimulatedLLM to demonstrate the full pipeline without any API calls.
Perfect for understanding the output structure before connecting a real model.
"""
from antcrew import DevTeam
from antcrew.models import SimulatedLLM

team = DevTeam(model=SimulatedLLM())
state = team.run("Build a REST API with JWT authentication")

# ── PRD ──────────────────────────────────────────────────────────────────────
if state.get("prd"):
    prd = state["prd"]
    print(f"\n=== PRD: {prd.title} ===")
    print(prd.summary)

# ── Tickets ───────────────────────────────────────────────────────────────────
if state.get("tickets"):
    print(f"\n=== Tickets ({len(state['tickets'])}) ===")
    for t in state["tickets"]:
        print(f"  [{t.priority.value:8}] {t.id}: {t.title}")

# ── Code artifacts ────────────────────────────────────────────────────────────
if state.get("code_artifacts"):
    print(f"\n=== Code files ({len(state['code_artifacts'])}) ===")
    for a in state["code_artifacts"]:
        print(f"  {a.file_path}  ({a.language})")
        print(f"  # {a.description}")
