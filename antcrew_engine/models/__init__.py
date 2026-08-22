from antcrew_engine.models.anthropic_model import AnthropicModel
from antcrew_engine.models.base import BaseLLM, Message
from antcrew_engine.models.cache import FileLLMCache, LLMCache
from antcrew_engine.models.comparison import ComparisonLLM
from antcrew_engine.models.fallback import FallbackLLM
from antcrew_engine.models.gemini_model import GeminiModel
from antcrew_engine.models.ollama_model import OllamaModel
from antcrew_engine.models.simulated import SimulatedLLM


# Optional-dependency models are imported lazily so the package can be
# imported without installing every supported provider SDK.
def __getattr__(name: str):
    if name == "OpenAIModel":
        from antcrew_engine.models.openai_model import OpenAIModel
        return OpenAIModel
    if name == "GroqModel":
        from antcrew_engine.models.groq_model import GroqModel
        return GroqModel
    raise AttributeError(f"module 'antcrew_engine.models' has no attribute {name!r}")

__all__ = [
    "BaseLLM", "Message",
    "LLMCache",
    "FileLLMCache",
    "AnthropicModel",
    "FallbackLLM",
    "ComparisonLLM",
    "OllamaModel",
    "GroqModel",   # lazy — requires pip install groq
    "GeminiModel",
    "SimulatedLLM",
    "OpenAIModel",  # lazy — requires pip install openai
]
