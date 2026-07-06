"""Engine capability fixtures for SimulatedLLM.

These are used by antcrew/capabilities/ executors.
Kept separate to avoid encoding issues in the main simulated.py file.
"""
from __future__ import annotations
import json

REQUIREMENTS = (
    "# Requirements\n\n"
    "## Objective\nBuild the requested software system.\n\n"
    "## Functional Requirements\n"
    "- MUST implement core business logic\n"
    "- MUST expose a clean public API\n"
    "- MUST handle error cases gracefully\n\n"
    "## Acceptance Criteria\n"
    "- All features implemented\n"
    "- All tests pass\n"
)

ARCHITECTURE = (
    "# Architecture\n\n"
    "## Components\n"
    "- src/core.py - Business logic\n"
    "- src/api.py  - Public interface\n\n"
    "## Directory Structure\n"
    "src/\n  core.py\n  api.py\n"
    "tests/\n  test_core.py\n"
)

TASKS = json.dumps([
    {
        "id": "task_001",
        "title": "Implement core module",
        "description": "Implement the primary business logic in src/core.py.",
        "files": ["src/core.py"],
        "depends_on": [],
    },
])

FILE_PLAN = json.dumps([
    {"file_path": "src/core.py", "description": "Core module implementation"},
])

FILE_CONTENT = json.dumps({
    "file_path": "src/core.py",
    "content": "def run(data=None):\n    return {\"status\": \"ok\", \"data\": data}\n",
})

TEST_CODE = (
    "def test_run_ok():\n"
    "    from src.core import run\n"
    "    assert run()[\"status\"] == \"ok\"\n"
    "\n"
    "def test_run_passthrough():\n"
    "    from src.core import run\n"
    "    assert run(42)[\"data\"] == 42\n"
)

REVIEW = json.dumps({
    "summary": "Code quality is acceptable. No critical issues found.",
    "verdict": "approved",
    "findings": [
        {
            "file": "src/core.py",
            "severity": "info",
            "message": "Consider adding module-level docstring.",
            "suggestion": "Add a one-line description at the top of the file.",
        }
    ],
})
