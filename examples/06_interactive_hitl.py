"""
Interactive (human-in-the-loop) mode.

The pipeline pauses after every agent. You can:
  approve        — continue to the next agent
  reject         — stop the pipeline immediately
  edit           — open the artifact in $EDITOR as JSON
  <any text>     — send as feedback; conversational agents revise in-place

Requires: ANTHROPIC_API_KEY (or swap for SimulatedLLM for a dry run)

Run:
    python examples/06_interactive_hitl.py
"""
from antcrew import DevTeam
from antcrew.models import AnthropicModel
from antcrew.utils.persistence import save_state

# Use SimulatedLLM to try without API keys
# team = DevTeam(model=SimulatedLLM())

team = DevTeam(model=AnthropicModel())
state = team.run_interactive("Add two-factor authentication to the login flow")

print("\nFinal state:")
if state.get("review"):
    print(f"  Code review: {state['review'].verdict.upper()}")
if state.get("code_artifacts"):
    print(f"  Code files:  {len(state['code_artifacts'])}")

save_state(state, "output/interactive_run.json")
print("State saved to output/interactive_run.json")
