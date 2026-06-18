"""
DevTeam with Claude — full pipeline: BA → PM → BackendDev → QA → Reviewer.

Requires: ANTHROPIC_API_KEY

Run:
    python examples/02_dev_team.py
"""
from antcrew import DevTeam
from antcrew.models import AnthropicModel
from antcrew.utils.persistence import save_state

team = DevTeam(model=AnthropicModel())
state = team.run("Build a REST API for a task management app with JWT authentication")

prd = state.get("prd")
if prd:
    print(f"\n{'='*60}")
    print(f"PRD: {prd.title}")
    print(f"{prd.summary}")
    print(f"{'='*60}\n")

for ticket in state.get("tickets") or []:
    print(f"  [{ticket.priority.value:6}] {ticket.id}: {ticket.title}")

review = state.get("review")
if review:
    print(f"\nCode review: {review.verdict.upper()} — {review.summary}")

save_state(state, "output/dev_team_run.json")
print("\nState saved to output/dev_team_run.json")
