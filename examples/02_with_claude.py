"""
02 — Run with Claude (real API, streaming)

Requires: export ANTHROPIC_API_KEY=sk-ant-...
"""
from antcrew import DevTeam
from antcrew.models import AnthropicModel

llm = AnthropicModel("claude-sonnet-4-6")

# Stream tokens to stdout as they arrive
llm.on_token = lambda token: print(token, end="", flush=True)

team = DevTeam(model=llm)
state = team.run("Build a user registration API with email verification")

print("\n")

# Show token cost
summary = llm.get_usage_summary()
print(f"Total tokens: {summary['total_input_tokens']} in / {summary['total_output_tokens']} out")
print(f"Estimated cost: ${summary['total_cost_usd']:.4f}")

# Print code file paths
if state.get("code_artifacts"):
    print(f"\nGenerated {len(state['code_artifacts'])} file(s):")
    for a in state["code_artifacts"]:
        print(f"  {a.file_path}")
