"""antcrew.models — re-exports from antcrew_engine.models (backward compatibility).

The canonical model implementations live in ``antcrew_engine.models``.
This shim keeps ``from antcrew.models import AnthropicModel`` working.
"""
from antcrew_engine.models.base import BaseLLM, Message
from antcrew_engine.models.cache import LLMCache, FileLLMCache
from antcrew_engine.models.anthropic_model import AnthropicModel
from antcrew_engine.models.fallback import FallbackLLM
from antcrew_engine.models.ollama_model import OllamaModel
from antcrew_engine.models.groq_model import GroqModel
from antcrew_engine.models.gemini_model import GeminiModel
from antcrew_engine.models.simulated import SimulatedLLM


def __getattr__(name: str):
    if name == "OpenAIModel":
        from antcrew_engine.models.openai_model import OpenAIModel
        return OpenAIModel
    if name == "AzureOpenAIModel":
        from antcrew_engine.models.azure_openai_model import AzureOpenAIModel
        return AzureOpenAIModel
    raise AttributeError(f"module 'antcrew.models' has no attribute {name!r}")


__all__ = [
    "BaseLLM", "Message",
    "LLMCache", "FileLLMCache",
    "AnthropicModel",
    "FallbackLLM",
    "OllamaModel",
    "GroqModel",
    "GeminiModel",
    "SimulatedLLM",
    "OpenAIModel",       # lazy — requires pip install openai
    "AzureOpenAIModel",  # lazy — requires pip install openai
]
