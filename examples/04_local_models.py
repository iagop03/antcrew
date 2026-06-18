"""
Fully local — no API keys, no code leaves your machine.

Options:
  - OllamaModel: requires Ollama running at http://localhost:11434
  - GroqModel:   requires GROQ_API_KEY (cloud but very fast and cheap)
  - OpenAIModel with base_url: DeepSeek, Mistral, LM Studio, llama.cpp

Run (with Ollama):
    ollama pull llama3
    python examples/04_local_models.py
"""
from antcrew import DevTeam
from antcrew.models import OllamaModel

# ── Ollama (local) ────────────────────────────────────────────────────────────
team = DevTeam(model=OllamaModel("llama3"))
state = team.run("Add input validation to the user registration endpoint")

print("\nOllama run complete.")
print(f"Code files: {len(state.get('code_artifacts') or [])}")
for a in state.get("code_artifacts") or []:
    print(f"  {a.file_path}")


# ── Groq (cloud, ultra-fast) ──────────────────────────────────────────────────
# from antcrew.models import GroqModel
#
# team = DevTeam(model=GroqModel("llama3-70b-8192"))
# state = team.run("Add input validation to the user registration endpoint")


# ── DeepSeek via OpenAI-compatible API ────────────────────────────────────────
# import os
# from antcrew.models import OpenAIModel
#
# team = DevTeam(model=OpenAIModel(
#     model="deepseek-chat",
#     base_url="https://api.deepseek.com/v1",
#     api_key=os.environ["DEEPSEEK_API_KEY"],
# ))


# ── LM Studio (local OpenAI-compatible server) ────────────────────────────────
# from antcrew.models import OpenAIModel
#
# team = DevTeam(model=OpenAIModel(
#     model="local-model",
#     base_url="http://localhost:1234/v1",
# ))


# ── Mix models per agent ──────────────────────────────────────────────────────
# from antcrew.models import AnthropicModel
# from antcrew.agents import PMAgent
#
# team = DevTeam(
#     model=OllamaModel("llama3"),          # default for coding agents
#     agents={"pm": PMAgent(llm=AnthropicModel())},  # Claude for PM only
# )
