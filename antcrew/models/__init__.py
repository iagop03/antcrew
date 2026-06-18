from antcrew.models.base import BaseLLM, Message
from antcrew.models.cache import LLMCache, FileLLMCache
from antcrew.models.anthropic_model import AnthropicModel
from antcrew.models.fallback import FallbackLLM
from antcrew.models.ollama_model import OllamaModel
from antcrew.models.groq_model import GroqModel
from antcrew.models.gemini_model import GeminiModel
from antcrew.models.simulated import SimulatedLLM

# OpenAIModel is imported lazily (requires optional 'openai' package)
def __getattr__(name: str):
    if name == "OpenAIModel":
        from antcrew.models.openai_model import OpenAIModel
        return OpenAIModel
    raise AttributeError(f"module 'antcrew.models' has no attribute {name!r}")

__all__ = [
    "BaseLLM", "Message",
    "LLMCache",
    "FileLLMCache",
    "AnthropicModel",
    "FallbackLLM",
    "OllamaModel",
    "GroqModel",
    "GeminiModel",
    "SimulatedLLM",
    "OpenAIModel",  # lazy — requires pip install openai
]
