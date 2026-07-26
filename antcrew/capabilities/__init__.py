"""antcrew.capabilities — re-exports from antcrew_engine.capabilities (backward compatibility).

The canonical implementation lives in the ``antcrew-engine`` package.
This shim ensures that existing code which imports from ``antcrew.capabilities``
continues to work without modification.
"""
from antcrew_engine.capabilities import (
    Architect,
    BugFixer,
    CodeGenerator,
    CodeRegenerator,
    CodeReviewer,
    DependencyInstaller,
    DocGenerator,
    HitlReviewer,
    ManualActionCapability,
    ReviewFixer,
    SecurityAuditor,
    SpecExtractor,
    TaskPlanner,
    TeamExecutor,
    TestGenerator,
    TestRunner,
)

__all__ = [
    "Architect",
    "BugFixer",
    "CodeGenerator",
    "CodeRegenerator",
    "CodeReviewer",
    "DependencyInstaller",
    "DocGenerator",
    "HitlReviewer",
    "ManualActionCapability",
    "ReviewFixer",
    "SecurityAuditor",
    "SpecExtractor",
    "TaskPlanner",
    "TeamExecutor",
    "TestGenerator",
    "TestRunner",
]
