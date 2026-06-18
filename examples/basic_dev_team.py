"""
Basic usage example — runs the DevTeam pipeline with Claude.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python examples/basic_dev_team.py

To use a local model instead (requires Ollama running):
    model = OllamaModel("llama3")
    team  = DevTeam(model=model)

To use Groq (requires GROQ_API_KEY):
    model = GroqModel("llama3-70b-8192")
    team  = DevTeam(model=model)
"""

from antcrew import DevTeam
from antcrew.models import AnthropicModel

team = DevTeam(model=AnthropicModel("claude-sonnet-4-6"))

result = team.run("Add a password reset flow to the auth module")

print("\n=== PRD ===")
print(result["prd"].model_dump_json(indent=2))

print("\n=== Tickets ===")
for ticket in result["tickets"] or []:
    print(f"  [{ticket.priority.value.upper()}] {ticket.id}: {ticket.title}")

print("\n=== Code artifacts ===")
for artifact in result["code_artifacts"] or []:
    print(f"  {artifact.file_path}  ({artifact.language})")
