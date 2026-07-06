"""antcrew.capabilities — concrete capability executors for the engine.

These import from antcrew.engine (interfaces) and never the reverse.
LLM dependencies are optional extras in pyproject.toml:
    pip install antcrew            # engine only, no LLM deps
    pip install antcrew[anthropic] # + Anthropic SDK
    pip install antcrew[openai]    # + OpenAI SDK
"""
from .architect import Architect
from .code_generator import CodeGenerator
from .code_reviewer import CodeReviewer
from .spec_extractor import SpecExtractor
from .task_planner import TaskPlanner
from .team_executor import TeamExecutor
from .test_generator import TestGenerator
from .test_runner import TestRunner

__all__ = [
    "SpecExtractor",
    "Architect",
    "TaskPlanner",
    "CodeGenerator",
    "TestGenerator",
    "TestRunner",
    "CodeReviewer",
    "TeamExecutor",
]
