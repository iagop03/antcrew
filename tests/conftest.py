"""Shared pytest fixtures and configuration for antcrew tests."""
from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_api: tests that call real LLM provider APIs — skipped when no provider key is set",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip @pytest.mark.real_api tests when no provider API key is available."""
    has_key = bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GROQ_API_KEY")
    )
    if has_key:
        return
    skip = pytest.mark.skip(reason="real_api tests require ANTHROPIC_API_KEY / OPENAI_API_KEY / GROQ_API_KEY")
    for item in items:
        if item.get_closest_marker("real_api"):
            item.add_marker(skip)
