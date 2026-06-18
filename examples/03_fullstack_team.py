"""
FullStackTeam — complete pipeline from requirements to Dockerfile and README.

BA → PM → BackendDev → FrontendDev → QA → Reviewer → DevOps → DocWriter

Requires: ANTHROPIC_API_KEY

Run:
    python examples/03_fullstack_team.py
"""
from antcrew import FullStackTeam
from antcrew.models import AnthropicModel
from antcrew.utils.persistence import save_state

team = FullStackTeam(model=AnthropicModel())
state = team.run("Build a Kanban board app with a FastAPI backend and React frontend")

print(f"\nTickets:  {len(state.get('tickets') or [])}")
print(f"Code:     {len(state.get('code_artifacts') or [])} files")
print(f"DevOps:   {len(state.get('devops_artifacts') or [])} files")
print(f"Docs:     {len(state.get('doc_artifacts') or [])} files")

review = state.get("review")
if review:
    symbol = {"approve": "✓", "request_changes": "~", "reject": "✗"}[review.verdict]
    print(f"Review:   {symbol} {review.verdict.upper()}")

print("\nGenerated files:")
for a in (state.get("code_artifacts") or []):
    print(f"  {a.file_path}")
for a in (state.get("devops_artifacts") or []):
    print(f"  {a.file_path}  (devops)")
for a in (state.get("doc_artifacts") or []):
    print(f"  {a.file_path}  (docs)")

save_state(state, "output/fullstack_run.json")
print("\nState saved to output/fullstack_run.json")
