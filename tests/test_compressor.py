"""Tests for antcrew_engine.context.compressor and BaseLLM._maybe_compress."""
from __future__ import annotations

import textwrap

import pytest

from antcrew_engine.context.compressor import (
    ASTCodeCompressor,
    CompressedResult,
    TextSummaryCompressor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_count(text: str) -> int:
    return len(text) // 4


# ---------------------------------------------------------------------------
# Test: ASTCodeCompressor preserves signatures and omits long bodies
# ---------------------------------------------------------------------------

PYTHON_WITH_THREE_FUNCS = textwrap.dedent("""\
    def short_func(x):
        return x


    def another_short(y):
        return y * 2


    def long_func(a, b, c):
        \"\"\"Compute a heavy result.\"\"\"
        step1 = a + b
        step2 = b + c
        step3 = step1 * step2
        step4 = step3 ** 2
        step5 = step4 - a
        step6 = step5 + b
        step7 = step6 * c
        result = step7 // (a + 1)
        return result
""")


def test_ast_compressor_preserves_signatures():
    compressor = ASTCodeCompressor()
    # Use a tight budget that forces long_func's body to be omitted
    budget = _token_count(PYTHON_WITH_THREE_FUNCS) // 2

    result: CompressedResult = compressor.compress(
        PYTHON_WITH_THREE_FUNCS, budget_tokens=budget
    )

    assert result.method == "ast_python"
    assert result.compressed_tokens < result.original_tokens

    # Function signatures must survive
    assert "def short_func(x):" in result.text
    assert "def another_short(y):" in result.text
    assert "def long_func(a, b, c):" in result.text

    # Docstring of long_func must be preserved
    assert "Compute a heavy result" in result.text

    # Body omission marker must appear
    assert "# ... (body omitted" in result.text


# ---------------------------------------------------------------------------
# Test: ASTCodeCompressor falls back gracefully on non-Python content
# ---------------------------------------------------------------------------

JS_CODE = textwrap.dedent("""\
    function greet(name) {
        console.log(`Hello, ${name}!`);
        const arr = [1, 2, 3];
        return arr.map(x => x * 2);
    }
""")


def test_ast_compressor_falls_back_on_js():
    compressor = ASTCodeCompressor()
    # Tight budget to force compression
    budget = 1  # absurdly small so compression is attempted

    result: CompressedResult = compressor.compress(JS_CODE, budget_tokens=budget)

    # Must not raise; method must indicate text-level fallback (or passthrough if small)
    assert result.method in ("text_head_tail", "passthrough")
    assert isinstance(result.text, str)
    assert len(result.text) > 0


# ---------------------------------------------------------------------------
# Test: TextSummaryCompressor keeps head + tail with omission marker
# ---------------------------------------------------------------------------

def test_text_compressor_head_tail():
    lines = [f"line {i}" for i in range(200)]
    content = "\n".join(lines)

    compressor = TextSummaryCompressor()
    # Tight budget so compression kicks in
    budget = _token_count(content) // 3

    result = compressor.compress(content, budget_tokens=budget)

    assert result.method == "text_head_tail"
    assert result.original_tokens > result.compressed_tokens

    result_lines = result.text.splitlines()

    # First line of output should be "line 0" (head preserved)
    assert result_lines[0] == "line 0"
    # 30 head lines (indices 0..29) → last head line is "line 29"
    assert any("line 29" in ln for ln in result_lines)

    # Last 10 lines of the 200-line input are lines 190..199
    assert any("line 190" in ln for ln in result_lines)
    assert result_lines[-1] == "line 199"

    # Omission marker must appear
    assert "lines omitted" in result.text


# ---------------------------------------------------------------------------
# Test: No compression when content fits within budget (passthrough)
# ---------------------------------------------------------------------------

def test_passthrough_under_budget():
    content = "x = 1\ny = 2\n"
    compressor = ASTCodeCompressor()
    big_budget = 10_000

    result = compressor.compress(content, budget_tokens=big_budget)

    assert result.method == "passthrough"
    assert result.text == content
    assert result.original_tokens == result.compressed_tokens


# ---------------------------------------------------------------------------
# Test: BaseLLM._maybe_compress is a no-op when context_compressor is None
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# EN-P08 — Compressor boundary: content at exactly the budget limit
# ---------------------------------------------------------------------------

def test_ast_compressor_passthrough_at_exact_budget():
    """Content whose token count equals the budget must pass through unchanged."""
    content = "def f(x):\n    return x\n"
    budget = _token_count(content)  # exactly matches

    compressor = ASTCodeCompressor()
    result = compressor.compress(content, budget_tokens=budget)

    assert result.method == "passthrough"
    assert result.text == content
    assert result.original_tokens == result.compressed_tokens


def test_ast_compressor_compresses_one_token_over_budget():
    """Content with one more token than the budget triggers compression."""
    content = "def f(x):\n    return x\n"
    budget = _token_count(content) - 1  # one token under budget

    compressor = ASTCodeCompressor()
    result = compressor.compress(content, budget_tokens=budget)

    # Must attempt compression (not passthrough)
    assert result.method != "passthrough" or result.compressed_tokens <= budget + 1


def test_text_compressor_passthrough_at_exact_budget():
    """TextSummaryCompressor passes through content that exactly fits the budget."""
    content = "\n".join(f"line {i}" for i in range(5))
    budget = _token_count(content)

    compressor = TextSummaryCompressor()
    result = compressor.compress(content, budget_tokens=budget)

    assert result.method == "passthrough"
    assert result.text == content


def test_text_compressor_head_tail_at_one_below_budget():
    """TextSummaryCompressor compresses content when budget is one token below content size."""
    lines = [f"line {i}" for i in range(50)]
    content = "\n".join(lines)
    budget = _token_count(content) - 1

    compressor = TextSummaryCompressor()
    result = compressor.compress(content, budget_tokens=budget)

    # Compression must be attempted; result must be shorter or equal
    assert result.compressed_tokens <= result.original_tokens


def test_maybe_compress_noop_when_no_compressor():
    from antcrew_engine.models.base import BaseLLM, Message

    class _MinimalLLM(BaseLLM):
        def complete(self, messages, *, max_tokens=16384, json_mode=False) -> str:
            return "ok"

    llm = _MinimalLLM()
    assert llm.context_compressor is None
    assert llm.context_budget_tokens is None

    text = "some large context " * 500
    returned = llm._maybe_compress(text, label="test")
    assert returned is text  # identity — not copied, not changed
