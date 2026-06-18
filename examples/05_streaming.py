"""
Streaming — watch tokens arrive in real time.

The on_token callback fires for each chunk of text from the LLM.
current_agent tells you which agent is currently generating.

Requires: ANTHROPIC_API_KEY

Run:
    python examples/05_streaming.py
"""
import sys
from antcrew import DevTeam
from antcrew.models import AnthropicModel

llm = AnthropicModel()

current: dict = {"agent": ""}

def on_token(token: str) -> None:
    if llm.current_agent != current["agent"]:
        if current["agent"]:
            print()  # newline between agents
        current["agent"] = llm.current_agent
        print(f"\n[{current['agent']}] ", end="", flush=True)
    print(token, end="", flush=True)

llm.on_token = on_token

team = DevTeam(model=llm)
state = team.run("Build a simple rate-limiting middleware for a FastAPI app")

print("\n\nDone.")
print(f"Tickets: {len(state.get('tickets') or [])}")
print(f"Files:   {len(state.get('code_artifacts') or [])}")
