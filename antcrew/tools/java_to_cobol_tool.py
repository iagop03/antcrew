"""JavaToCOBOLTool — antcrew BaseTool wrapping the polytranslate translator."""
from __future__ import annotations

import json
from typing import Optional

from antcrew.core.tools import BaseTool, ToolResult


class JavaToCOBOLTool(BaseTool):
    """Translate Java source code to COBOL using an LLM.

    The tool accepts a JSON input with keys:
      - ``java_code`` (required): Java source string
      - ``standards_file`` (optional): path to a .cbl or .md standards file
      - ``feedback`` (optional): if provided alongside ``current_cobol``, refines
        a previous translation instead of starting from scratch
      - ``current_cobol`` (optional): previous COBOL output to refine

    Returns the generated COBOL string (or a validation warning + COBOL).

    Usage in an agent::

        from antcrew.tools.java_to_cobol_tool import JavaToCOBOLTool

        tool = JavaToCOBOLTool(llm=my_llm)
        agent = MigrationAgent(llm, tools=[tool])

    Requires polytranslate: pip install polytranslate
    """

    name = "java_to_cobol"
    description = (
        "Translate Java source code to COBOL. "
        "Input: JSON with 'java_code' (required), 'standards_file' (optional path to .cbl/.md), "
        "'feedback' + 'current_cobol' (optional, for refining a previous translation). "
        "Returns the generated COBOL code."
    )

    def __init__(self, llm=None, standards_file: Optional[str] = None) -> None:
        """
        Args:
            llm: Any LangChain-compatible LLM, or None to auto-detect via from_env().
            standards_file: Default standards file path (can be overridden per-call).
        """
        self._llm = llm
        self._default_standards_file = standards_file

    def run(self, input: str) -> ToolResult:
        try:
            from polytranslate.translators.java_to_cobol import JavaToCOBOLTranslator
        except ImportError:
            return ToolResult(
                output="",
                error="polytranslate is not installed. Run: pip install polytranslate",
            )

        # Parse input — accept plain Java string or JSON dict
        try:
            data = json.loads(input)
            java_code = data.get("java_code", "")
            standards_file = data.get("standards_file") or self._default_standards_file
            feedback = data.get("feedback")
            current_cobol = data.get("current_cobol")
        except (json.JSONDecodeError, AttributeError):
            java_code = input.strip()
            standards_file = self._default_standards_file
            feedback = None
            current_cobol = None

        if not java_code.strip():
            return ToolResult(output="", error="java_code is empty")

        try:
            if self._llm is not None:
                translator = JavaToCOBOLTranslator(llm=self._llm)
            else:
                translator = JavaToCOBOLTranslator.from_env()
        except EnvironmentError as exc:
            return ToolResult(output="", error=str(exc))

        if standards_file:
            try:
                translator.load_standards(standards_file)
            except Exception as exc:
                return ToolResult(output="", error=f"Failed to load standards: {exc}")

        try:
            if feedback and current_cobol:
                cobol = translator.refine(java_code, current_cobol, feedback)
            else:
                cobol = translator.translate(java_code)
        except TimeoutError as exc:
            return ToolResult(output="", error=str(exc))
        except Exception as exc:
            return ToolResult(output="", error=f"Translation error: {exc}")

        validation = translator.validate(cobol)
        prefix = ""
        if validation.errors:
            prefix = "VALIDATION ERRORS:\n" + "\n".join(f"  ✗ {e}" for e in validation.errors) + "\n\n"
        elif validation.warnings:
            prefix = "WARNINGS:\n" + "\n".join(f"  ⚠ {w}" for w in validation.warnings) + "\n\n"

        return ToolResult(output=prefix + cobol)
