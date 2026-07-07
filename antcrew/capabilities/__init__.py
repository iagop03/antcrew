"""antcrew.capabilities — concrete capability executors for the engine.

These import from antcrew.engine (interfaces) and never the reverse.
LLM dependencies are optional extras in pyproject.toml:
    pip install antcrew            # engine only, no LLM deps
    pip install antcrew[anthropic] # + Anthropic SDK
    pip install antcrew[openai]    # + OpenAI SDK
"""
from .architect import Architect
from .bug_fixer import BugFixer
from .code_generator import CodeGenerator
from .code_regenerator import CodeRegenerator
from .code_reviewer import CodeReviewer
from .dependency_installer import DependencyInstaller
from .doc_generator import DocGenerator
from .hitl_reviewer import HitlReviewer
from .review_fixer import ReviewFixer
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
    "CodeRegenerator",
    "DependencyInstaller",
    "DocGenerator",
    "HitlReviewer",
    "ReviewFixer",
    "TestGenerator",
    "TestRunner",
    "BugFixer",
    "CodeReviewer",
    "TeamExecutor",
]
