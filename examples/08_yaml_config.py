"""
YAML config — load a team from agentteam.yaml instead of Python.

agentteam.yaml supports:
  team:    dev | fullstack | research | content
  model:   claude | gpt-4o | gemini | ollama:<name> | groq:<name> | simulated
  flow:    custom pipeline order (list of [src, dst] pairs)
  channel: telegram | slack | console   (HITL notifications)
  channels: list of channels
  agents:  per-agent overrides (model, approval_required, response_options)

Run:
    python examples/08_yaml_config.py
"""
import tempfile
import textwrap
from pathlib import Path

from antcrew.config import load

# Write a temporary config for this demo
yaml_content = textwrap.dedent("""\
    team: dev
    model: simulated

    agents:
      pm:
        approval_required: true
        response_options: [approve, reject]
      backend_dev:
        approval_required: true

    channel:
      type: console
""")

with tempfile.NamedTemporaryFile(
    mode="w", suffix=".yaml", delete=False, encoding="utf-8"
) as f:
    f.write(yaml_content)
    config_path = Path(f.name)

team = load(config_path)
print(f"Loaded team: {type(team).__name__}")
print(f"LLM: {type(team.llm).__name__}")
print(f"Integrations: {[type(i).__name__ for i in team.integrations]}")

# Run without HITL for this demo (use team.run_interactive() for the full loop)
state = team.run("Build a health check endpoint")
print(f"\nTickets generated: {len(state.get('tickets') or [])}")
print(f"Code files:        {len(state.get('code_artifacts') or [])}")
