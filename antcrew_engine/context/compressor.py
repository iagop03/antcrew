from __future__ import annotations

import ast
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)


def _token_count(text: str) -> int:
    """Cheap token estimator: ~4 chars per token (sufficient for budget heuristics)."""
    return len(text) // 4


@dataclass
class CompressedResult:
    text: str
    original_tokens: int
    compressed_tokens: int
    method: str  # "ast_python" | "text_head_tail" | "passthrough"


class ContextCompressor(ABC):
    @abstractmethod
    def compress(self, content: str, *, budget_tokens: int, file_path: str = "") -> CompressedResult:
        ...


class TextSummaryCompressor(ContextCompressor):
    """For logs/long text: keep first HEAD lines + last TAIL lines, indicate how many were skipped."""

    HEAD_LINES = 30
    TAIL_LINES = 10

    def compress(self, content: str, *, budget_tokens: int, file_path: str = "") -> CompressedResult:
        original_tokens = _token_count(content)

        if original_tokens <= budget_tokens:
            return CompressedResult(
                text=content,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                method="passthrough",
            )

        lines = content.splitlines()
        total = len(lines)

        # Adjust head/tail counts upward if budget is generous enough
        head = self.HEAD_LINES
        tail = self.TAIL_LINES

        if total <= head + tail:
            # Not enough lines to split; return as-is (already over budget but can't do better)
            return CompressedResult(
                text=content,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                method="passthrough",
            )

        skipped = total - head - tail
        separator = f"\n# ... ({skipped} lines omitted) ...\n"
        result_text = "\n".join(lines[:head]) + separator + "\n".join(lines[-tail:])

        compressed_tokens = _token_count(result_text)
        return CompressedResult(
            text=result_text,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            method="text_head_tail",
        )


class ASTCodeCompressor(ContextCompressor):
    """For Python: parse with ast, preserve signatures/docstrings/imports, replace long bodies.

    Falls back to TextSummaryCompressor when the content cannot be parsed as Python
    (e.g. JavaScript, TypeScript, plain text).

    Omission priority (to stay within budget_tokens):
      1. Bodies of private functions/methods (names starting with ``_``) first.
      2. Bodies of public functions/methods next.
      Imports and class-level signatures are always preserved.
    """

    def compress(self, content: str, *, budget_tokens: int, file_path: str = "") -> CompressedResult:
        original_tokens = _token_count(content)

        if original_tokens <= budget_tokens:
            return CompressedResult(
                text=content,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                method="passthrough",
            )

        try:
            tree = ast.parse(content)
        except SyntaxError:
            log.debug("ASTCodeCompressor: SyntaxError on %r — falling back to TextSummaryCompressor", file_path)
            return TextSummaryCompressor().compress(content, budget_tokens=budget_tokens, file_path=file_path)

        lines = content.splitlines(keepends=True)

        # Collect all function/async-function defs via tree walk
        all_fns: list[ast.FunctionDef | ast.AsyncFunctionDef] = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        # Priority: private (_xxx) bodies omitted before public ones
        private_fns = [f for f in all_fns if f.name.startswith("_")]
        public_fns = [f for f in all_fns if not f.name.startswith("_")]

        omit_ids: set[int] = set()
        for candidate in private_fns + public_fns:
            omit_ids.add(id(candidate))
            if _token_count(self._rebuild(lines, all_fns, omit_ids)) <= budget_tokens:
                break

        final_text = self._rebuild(lines, all_fns, omit_ids)
        compressed_tokens = _token_count(final_text)

        return CompressedResult(
            text=final_text,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            method="ast_python",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _body_omit_range(
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[int, int] | None:
        """Return (start_line, end_line) of the body region to omit, or None.

        ``start_line`` is the first line whose content should be replaced.
        ``end_line`` is the last line (both 1-indexed, inclusive).

        The function signature (def …:) and optional leading docstring are
        always kept.  If there is nothing else to omit, returns None.
        """
        body = fn.body
        if not body:
            return None

        first_stmt = body[0]
        is_docstring = (
            isinstance(first_stmt, ast.Expr)
            and isinstance(getattr(first_stmt, "value", None), ast.Constant)
            and isinstance(first_stmt.value.value, str)
        )

        if is_docstring:
            if len(body) <= 1:
                # Only a docstring — nothing left to omit
                return None
            omit_start = first_stmt.end_lineno + 1
        else:
            omit_start = first_stmt.lineno

        omit_end = fn.end_lineno  # type: ignore[attr-defined]
        if omit_start > omit_end:
            return None

        return omit_start, omit_end

    def _rebuild(
        self,
        lines: list[str],
        all_fns: list[ast.FunctionDef | ast.AsyncFunctionDef],
        omit_ids: set[int],
    ) -> str:
        """Reconstruct source, replacing bodies of functions in omit_ids with a comment."""
        # Build start_line -> (end_line, replacement_comment)
        omit_map: dict[int, tuple[int, str]] = {}

        for fn in all_fns:
            if id(fn) not in omit_ids:
                continue
            rang = self._body_omit_range(fn)
            if rang is None:
                continue
            omit_start, omit_end = rang
            if omit_start in omit_map:
                # Another (outer) range already covers this start — skip
                continue

            n_lines = omit_end - omit_start + 1
            raw_line = lines[omit_start - 1] if omit_start <= len(lines) else ""
            stripped = raw_line.lstrip()
            indent = raw_line[: len(raw_line) - len(stripped)] if stripped else "    "
            comment = f"{indent}# ... (body omitted, {n_lines} lines)\n"
            omit_map[omit_start] = (omit_end, comment)

        result: list[str] = []
        i = 1  # 1-indexed line counter
        while i <= len(lines):
            if i in omit_map:
                end, comment = omit_map[i]
                result.append(comment)
                i = end + 1
            else:
                result.append(lines[i - 1])
                i += 1

        return "".join(result)
