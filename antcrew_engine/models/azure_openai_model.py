from __future__ import annotations

import os
from typing import Optional

from antcrew_engine.models.openai_model import OpenAIModel

try:
    from openai import AzureOpenAI  # type: ignore[import]
except ImportError:
    AzureOpenAI = None  # type: ignore[assignment,misc]


class AzureOpenAIModel(OpenAIModel):
    """Azure OpenAI endpoint adapter.

    Inherits all streaming, retry, cost-tracking, and reasoning-model logic
    from :class:`OpenAIModel`.  The only difference is the client: Azure uses
    a deployment name (``model``) instead of the canonical model id, and
    requires an ``azure_endpoint`` and ``api_version``.

    Required env vars (when not passed explicitly):
        ``AZURE_OPENAI_API_KEY``    — your Azure resource key
        ``AZURE_OPENAI_ENDPOINT``   — e.g. ``https://my-resource.openai.azure.com``
        ``AZURE_OPENAI_API_VERSION`` — e.g. ``2024-02-01`` (optional, has default)

    Usage::

        llm = AzureOpenAIModel("gpt-4o")          # reads env vars
        llm = AzureOpenAIModel(
            deployment="gpt-4o-prod",
            azure_endpoint="https://corp.openai.azure.com",
            api_key="abc...",
            api_version="2024-05-01-preview",
        )
        # Reasoning models work the same way:
        llm = AzureOpenAIModel("o3-mini-deployment")
    """

    _DEFAULT_API_VERSION = "2024-02-01"

    def __init__(
        self,
        deployment: str = "gpt-4o",
        *,
        azure_endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        organization: Optional[str] = None,
    ) -> None:
        if AzureOpenAI is None:
            raise ImportError(
                "openai package is required for AzureOpenAIModel. "
                "Install it: pip install antcrew[openai]"
            )

        resolved_endpoint = (
            azure_endpoint
            or os.environ.get("AZURE_OPENAI_ENDPOINT")
            or os.environ.get("OPENAI_AZURE_ENDPOINT")
        )
        if not resolved_endpoint:
            raise EnvironmentError(
                "Azure OpenAI endpoint is not set.\n"
                "  Pass azure_endpoint= or set AZURE_OPENAI_ENDPOINT.\n"
                "  Example: https://my-resource.openai.azure.com"
            )

        resolved_key = (
            api_key
            or os.environ.get("AZURE_OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        resolved_version = (
            api_version
            or os.environ.get("AZURE_OPENAI_API_VERSION")
            or self._DEFAULT_API_VERSION
        )

        self._model = deployment
        self._is_reasoning = any(
            deployment.lower().startswith(p) for p in ("o1", "o3")
        )

        client_kwargs: dict = {
            "api_key": resolved_key or "not-needed",
            "azure_endpoint": resolved_endpoint,
            "api_version": resolved_version,
        }
        if organization:
            client_kwargs["organization"] = organization

        self._client = AzureOpenAI(**client_kwargs)
        self._azure_endpoint = resolved_endpoint
        self._api_version = resolved_version
