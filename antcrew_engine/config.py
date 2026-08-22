"""antcrew_engine.config — minimal config helpers for the standalone engine.

Only contains ``build_llm()``.  Use ``antcrew.config`` for the full loader
(team context, channels, LangGraph runners) when working with Layer 1.
"""
from __future__ import annotations

from typing import Optional

from antcrew_engine.models.base import BaseLLM


def build_llm(model_str: str, *, prompt_caching: bool = False, api_key: Optional[str] = None, base_url: Optional[str] = None) -> BaseLLM:
    """Parse a model string and return a configured LLM instance.

    Supported forms::

        "claude"                      # default Anthropic (claude-sonnet-4-6)
        "claude-haiku-4-5-20251001"
        "gpt-4o"                      # OpenAI (requires openai extra)
        "openai:gpt-4o-mini"
        "ollama:llama3"               # local Ollama server
        "groq:llama3-70b-8192"
        "azure:my-deployment"
        "gemini"                      # GeminiModel default
        "moonshot:moonshot-v1-8k"     # Moonshot AI / Kimi
        "deepseek:deepseek-chat"      # DeepSeek
        "mistral:mistral-large-latest"
        "xai:grok-2-latest"           # xAI
        "together:meta-llama/Llama-3-70b-chat-hf"
        "fireworks:accounts/fireworks/models/llama-v3-70b-instruct"
        "cerebras:llama3.1-70b"
        "lmstudio:model-name"         # LM Studio (local, keyless)
        "vllm:model-name"             # vLLM (local/self-hosted, keyless)
        "simulated"                   # deterministic stub for tests
    """
    s = model_str.strip().lower()

    if s == "simulated":
        from antcrew_engine.models.simulated import SimulatedLLM
        return SimulatedLLM()

    if s.startswith("ollama:"):
        from antcrew_engine.models.ollama_model import OllamaModel
        kw: dict = {}
        if base_url:
            kw["base_url"] = base_url
        return OllamaModel(s.split(":", 1)[1], **kw)

    if s.startswith("groq:"):
        if base_url:
            from antcrew_engine.models.openai_model import OpenAIModel
            kw = {"base_url": base_url}
            if api_key:
                kw["api_key"] = api_key
            return OpenAIModel(s.split(":", 1)[1], **kw)
        from antcrew_engine.models.groq_model import GroqModel
        kw = {}
        if api_key:
            kw["api_key"] = api_key
        return GroqModel(s.split(":", 1)[1], **kw)

    if s.startswith("azure:"):
        from antcrew_engine.models.azure_openai_model import AzureOpenAIModel
        return AzureOpenAIModel(deployment=s.split(":", 1)[1])

    if s.startswith("openai:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw: dict = {}
        if api_key:
            kw["api_key"] = api_key
        if base_url:
            kw["base_url"] = base_url
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("moonshot:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {"base_url": base_url or "https://api.moonshot.cn/v1"}
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("deepseek:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {"base_url": base_url or "https://api.deepseek.com/v1"}
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("mistral:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {"base_url": base_url or "https://api.mistral.ai/v1"}
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("xai:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {"base_url": base_url or "https://api.x.ai/v1"}
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("together:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {"base_url": base_url or "https://api.together.xyz/v1"}
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("fireworks:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {"base_url": base_url or "https://api.fireworks.ai/inference/v1"}
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("cerebras:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {"base_url": base_url or "https://api.cerebras.ai/v1"}
        if api_key:
            kw["api_key"] = api_key
        return OpenAIModel(s.split(":", 1)[1], **kw)

    if s.startswith("lmstudio:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        # LM Studio accepts any non-empty string as api_key
        return OpenAIModel(s.split(":", 1)[1],
                           api_key=api_key or "lm-studio",
                           base_url=base_url or "http://localhost:1234/v1")

    if s.startswith("vllm:"):
        from antcrew_engine.models.openai_model import OpenAIModel
        # vLLM accepts any non-empty string as api_key
        return OpenAIModel(s.split(":", 1)[1],
                           api_key=api_key or "vllm",
                           base_url=base_url or "http://localhost:8000/v1")

    if s.startswith("gpt") or s.startswith("o1") or s.startswith("o3") or s.startswith("o4"):
        from antcrew_engine.models.openai_model import OpenAIModel
        kw = {}
        if api_key:
            kw["api_key"] = api_key
        if base_url:
            kw["base_url"] = base_url
        return OpenAIModel(s, **kw)

    if s.startswith("gemini"):
        if base_url:
            # Proxy/BYOK with custom endpoint: use OpenAI-compatible interface
            from antcrew_engine.models.openai_model import OpenAIModel
            model_id = "gemini-1.5-flash" if s == "gemini" else s
            return OpenAIModel(model_id, api_key=api_key or "gemini", base_url=base_url)
        from antcrew_engine.models.gemini_model import GeminiModel
        gemini_kw: dict = {}
        if api_key:
            gemini_kw["api_key"] = api_key
        return GeminiModel(**gemini_kw) if s == "gemini" else GeminiModel(s, **gemini_kw)

    # Default: Anthropic / Claude (model strings starting with "claude" or the bare "anthropic")
    if not (s.startswith("claude") or s == "anthropic"):
        raise ValueError(
            f"Unknown model: {model_str!r}. "
            "Supported prefixes: claude, gpt, o1, o3, o4, openai:, ollama:, groq:, azure:, gemini, "
            "moonshot:, deepseek:, mistral:, xai:, together:, fireworks:, cerebras:, lmstudio:, vllm:, simulated."
        )
    from antcrew_engine.models.anthropic_model import AnthropicModel
    model_id = None if s in ("claude", "anthropic") else s
    kw = {}
    if model_id:
        kw["model"] = model_id
    if api_key:
        kw["api_key"] = api_key
    if base_url:
        kw["base_url"] = base_url
    return AnthropicModel(prompt_caching=prompt_caching, **kw)
