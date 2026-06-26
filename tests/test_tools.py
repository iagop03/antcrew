"""Tests for BaseTool, built-in tools, and the ReAct loop in BaseAgent."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from antcrew.core.tools import (
    BaseTool,
    CodeExecutorTool,
    ReadFileTool,
    ToolResult,
    WebSearchTool,
)
from antcrew.testing.llms import SequencedLLM


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

def test_tool_result_ok():
    r = ToolResult("hello")
    assert r.ok
    assert str(r) == "hello"


def test_tool_result_error():
    r = ToolResult("", error="boom")
    assert not r.ok
    assert "boom" in str(r)


# ---------------------------------------------------------------------------
# BaseTool.schema()
# ---------------------------------------------------------------------------

def test_base_tool_schema():
    class _T(BaseTool):
        name = "my_tool"
        description = "does stuff"
        def run(self, input): return ToolResult(input)

    assert "my_tool" in _T().schema()
    assert "does stuff" in _T().schema()


# ---------------------------------------------------------------------------
# CodeExecutorTool
# ---------------------------------------------------------------------------

def test_code_executor_runs_python():
    tool = CodeExecutorTool()
    result = tool.run("print('hello')")
    assert result.ok
    assert "hello" in result.output


def test_code_executor_captures_stderr():
    tool = CodeExecutorTool()
    result = tool.run("import sys; sys.stderr.write('err\\n')")
    assert "err" in result.output or not result.ok


def test_code_executor_reports_runtime_error():
    tool = CodeExecutorTool()
    result = tool.run("raise ValueError('oops')")
    assert not result.ok
    assert "oops" in result.error


def test_code_executor_timeout():
    tool = CodeExecutorTool(timeout=0.3)
    result = tool.run("import time; time.sleep(10)")
    assert not result.ok
    assert "Timed out" in result.error


def test_code_executor_truncates_long_output():
    tool = CodeExecutorTool(max_output=50)
    result = tool.run("print('x' * 200)")
    assert result.ok
    assert "truncated" in result.output


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------

def test_read_file_reads_existing(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    tool = ReadFileTool()
    result = tool.run(str(f))
    assert result.ok
    assert "hello world" in result.output


def test_read_file_missing_returns_error(tmp_path):
    tool = ReadFileTool()
    result = tool.run(str(tmp_path / "nope.txt"))
    assert not result.ok
    assert "not found" in result.error.lower()


def test_read_file_root_resolves_relative(tmp_path):
    (tmp_path / "data.txt").write_text("data", encoding="utf-8")
    tool = ReadFileTool(root=str(tmp_path))
    result = tool.run("data.txt")
    assert result.ok
    assert "data" in result.output


def test_read_file_truncates_large_file(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 10_000, encoding="utf-8")
    tool = ReadFileTool(max_chars=100)
    result = tool.run(str(f))
    assert result.ok
    assert "truncated" in result.output
    assert len(result.output) < 200


# ---------------------------------------------------------------------------
# WebSearchTool (unit — mocked network)
# ---------------------------------------------------------------------------

def test_web_search_returns_result(monkeypatch):
    import json as _json
    import io

    fake_data = {
        "AbstractText": "Python is a programming language.",
        "RelatedTopics": [
            {"Text": "Guido van Rossum created Python."},
        ],
    }

    class _FakeResp:
        def read(self): return _json.dumps(fake_data).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: _FakeResp(),
    )
    tool = WebSearchTool()
    result = tool.run("python programming")
    assert result.ok
    assert "Python" in result.output


def test_web_search_network_error(monkeypatch):
    def _boom(url, timeout=None):
        raise OSError("network unreachable")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    tool = WebSearchTool()
    result = tool.run("anything")
    assert not result.ok
    assert "network unreachable" in result.error


def test_web_search_empty_response(monkeypatch):
    import json as _json

    class _Empty:
        def read(self): return _json.dumps({"AbstractText": "", "RelatedTopics": []}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Empty())
    tool = WebSearchTool()
    result = tool.run("obscure query xyz123")
    assert result.ok
    assert "No results" in result.output


# ---------------------------------------------------------------------------
# ReAct loop — system_with_tools()
# ---------------------------------------------------------------------------

def _make_agent(responses: list[str], tools=None):
    """Helper: BackendDevAgent with SequencedLLM and optional tools."""
    from antcrew.agents.backend_dev import BackendDevAgent
    llm = SequencedLLM(responses)
    return BackendDevAgent(llm, tools=tools or [])


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echoes input back."
    calls: list[str]

    def __init__(self):
        self.calls = []

    def run(self, input: str) -> ToolResult:
        self.calls.append(input)
        return ToolResult(f"ECHO:{input}")


def test_system_with_tools_no_tools_falls_through():
    """system_with_tools without tools behaves like system()."""
    agent = _make_agent(["direct answer"])
    result = agent.system_with_tools("sys", "user")
    assert result == "direct answer"


def test_system_with_tools_single_tool_call():
    """One tool call then a final answer — tool is invoked once."""
    tool = _EchoTool()
    responses = [
        "<tool_call><name>echo</name><input>hello</input></tool_call>",
        "Final answer after tool.",
    ]
    agent = _make_agent(responses, tools=[tool])
    result = agent.system_with_tools("sys", "user", tools=[tool])
    assert result == "Final answer after tool."
    assert tool.calls == ["hello"]


def test_system_with_tools_tool_result_injected():
    """The tool result is passed back to the LLM in the next call."""
    tool = _EchoTool()
    responses = [
        "<tool_call><name>echo</name><input>ping</input></tool_call>",
        "Done.",
    ]
    agent = _make_agent(responses, tools=[tool])
    # Second call (the "Done." response) will receive history containing ECHO:ping
    # We verify by checking the LLM call count (2 calls = 1 tool step + 1 final)
    agent.system_with_tools("sys", "user", tools=[tool])
    assert agent.llm.call_count == 2


def test_system_with_tools_unknown_tool_does_not_crash():
    """Calling an unknown tool name returns an error result and continues."""
    tool = _EchoTool()
    responses = [
        "<tool_call><name>unknown_tool</name><input>x</input></tool_call>",
        "Recovered.",
    ]
    agent = _make_agent(responses, tools=[tool])
    result = agent.system_with_tools("sys", "user", tools=[tool])
    assert result == "Recovered."
    assert tool.calls == []  # unknown tool was not called


def test_system_with_tools_max_steps_exhausted():
    """Loop exits after max_tool_steps: exactly (steps + 1) LLM calls are made."""
    tool = _EchoTool()
    # Always return a tool call — never a final answer
    responses = ["<tool_call><name>echo</name><input>x</input></tool_call>"] * 10
    agent = _make_agent(responses, tools=[tool])
    agent.system_with_tools("sys", "user", tools=[tool], max_tool_steps=3)
    # 3 tool steps + 1 final call = 4 total
    assert agent.llm.call_count == 4
    # Tool was called once per loop step
    assert len(tool.calls) == 3


def test_system_with_tools_respects_instance_tools():
    """Tools set on agent instance are used when no override passed."""
    tool = _EchoTool()
    responses = [
        "<tool_call><name>echo</name><input>hi</input></tool_call>",
        "Done.",
    ]
    agent = _make_agent(responses, tools=[tool])
    agent.system_with_tools("sys", "user")  # no explicit tools= override
    assert tool.calls == ["hi"]


def test_system_with_tools_no_call_returns_immediately():
    """If the first response has no tool_call, return it directly."""
    tool = _EchoTool()
    agent = _make_agent(["plain answer"], tools=[tool])
    result = agent.system_with_tools("sys", "user")
    assert result == "plain answer"
    assert agent.llm.call_count == 1
    assert tool.calls == []


# ---------------------------------------------------------------------------
# tools= on BaseAgent.__init__
# ---------------------------------------------------------------------------

def test_agent_tools_param_stored():
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.models.simulated import SimulatedLLM
    tool = _EchoTool()
    agent = BackendDevAgent(SimulatedLLM(), tools=[tool])
    assert agent.tools == [tool]


def test_agent_default_tools_empty():
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.models.simulated import SimulatedLLM
    agent = BackendDevAgent(SimulatedLLM())
    assert agent.tools == []
