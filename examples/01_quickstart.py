"""
Quickstart — zero setup required (SimulatedLLM, no API key).

Run:
    python examples/01_quickstart.py
"""
from antcrew import DevTeam
from antcrew.models import SimulatedLLM

team = DevTeam(model=SimulatedLLM())
state = team.run("Add a password reset flow to the auth module")

prd = state.get("prd")
if prd:
    print(f"\nPRD: {prd.title}")
    print(f"     {prd.summary}\n")

for ticket in state.get("tickets") or []:
    print(f"  [{ticket.priority.value:6}] {ticket.id}: {ticket.title}")

print(f"\nCode files: {len(state.get('code_artifacts') or [])}")
for a in state.get("code_artifacts") or []:
    print(f"  {a.file_path}")
