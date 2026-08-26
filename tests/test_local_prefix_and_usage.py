"""Tests for antcrew SDK: local: prefix, extra_body passthrough, RunResult.usage.

LU01  build_llm("local:claude-code:default") does not raise ValueError
LU02  build_llm with local: prefix uses AnthropicModel (passes to proxy base_url)
LU03  build_llm forwards extra_body to engine build_llm
LU04  RunResult.usage defaults to empty dict
LU05  RunResult.to_dict() includes usage field
LU06  RunResult.usage populated when explicitly set
LU07  build_llm("local:gemini") does not raise ValueError
LU08  build_llm("local:codex:default") does not raise ValueError
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from antcrew.core.run_result import RunResult


# ---------------------------------------------------------------------------
# LU04 — RunResult.usage defaults to {}
# ---------------------------------------------------------------------------

def test_lu04_run_result_usage_defaults_empty():
    result = RunResult(state={"_run_id": "x"})
    assert result.usage == {}


# ---------------------------------------------------------------------------
# LU05 — to_dict includes usage key
# ---------------------------------------------------------------------------

def test_lu05_run_result_to_dict_includes_usage():
    result = RunResult(state={"_run_id": "x"}, cost_usd=0.01)
    d = result.to_dict()
    assert "usage" in d
    assert d["usage"] == {}


# ---------------------------------------------------------------------------
# LU06 — usage can be set explicitly
# ---------------------------------------------------------------------------

def test_lu06_run_result_usage_explicit():
    usage = {
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "total_cost_usd": 0.005,
        "by_agent": [{"agent": "DevAgent", "input": 100, "output": 50}],
    }
    result = RunResult(state={}, usage=usage)
    assert result.usage["total_input_tokens"] == 100
    d = result.to_dict()
    assert d["usage"]["total_output_tokens"] == 50


# ---------------------------------------------------------------------------
# LU01/LU07/LU08 — local: prefix does not raise ValueError
# ---------------------------------------------------------------------------

def _fake_anthropic_model(**kw):
    m = MagicMock()
    m.max_cost_usd = None
    return m


def _patch_anthropic():
    return patch("antcrew_engine.models.anthropic_model.AnthropicModel", _fake_anthropic_model)


@pytest.mark.parametrize("model_str", [
    "local:claude-code:default",
    "local:gemini",
    "local:codex:default",
    "local:claude-opus-5",
])
def test_lu01_local_prefix_no_value_error(model_str):
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
        with _patch_anthropic():
            from antcrew.config import build_llm
            # Should not raise ValueError("Unknown model")
            try:
                llm = build_llm(model_str, base_url="http://localhost:3001")
                assert llm is not None
            except ValueError as e:
                if "Unknown model" in str(e):
                    pytest.fail(f"build_llm raised Unknown model for {model_str!r}: {e}")
                raise


# ---------------------------------------------------------------------------
# LU02 — local: prefix uses AnthropicModel
# ---------------------------------------------------------------------------

def test_lu02_local_prefix_uses_anthropic_model():
    created_class = {}

    class _FakeAnthropicModel:
        def __init__(self, **kw):
            created_class["kw"] = kw

    with patch("antcrew_engine.models.anthropic_model.AnthropicModel", _FakeAnthropicModel):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            from antcrew.config import build_llm
            build_llm("local:claude-code:default", base_url="http://localhost:3001")

    # The model should have been built with a base_url
    assert "base_url" in created_class.get("kw", {})


# ---------------------------------------------------------------------------
# LU03 — build_llm forwards extra_body to engine build_llm
# ---------------------------------------------------------------------------

def test_lu03_extra_body_forwarded():
    engine_call_kw = {}

    def _fake_engine_build_llm(model_str, **kw):
        engine_call_kw.update(kw)
        m = MagicMock()
        m.max_cost_usd = None
        return m

    with patch("antcrew_engine.config.build_llm", _fake_engine_build_llm):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            from antcrew import config as antcrew_cfg
            import importlib
            importlib.reload(antcrew_cfg)
            antcrew_cfg.build_llm(
                "claude",
                extra_body={"working_directory": "/ws/project"},
            )

    assert engine_call_kw.get("extra_body") == {"working_directory": "/ws/project"}
