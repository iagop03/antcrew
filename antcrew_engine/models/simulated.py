from __future__ import annotations

import json

from antcrew_engine.models._engine_fixtures import ARCHITECTURE as _ENG_ARCH
from antcrew_engine.models._engine_fixtures import FILE_CONTENT as _ENG_FILE_CONTENT
from antcrew_engine.models._engine_fixtures import FILE_PLAN as _ENG_FILE_PLAN
from antcrew_engine.models._engine_fixtures import REQUIREMENTS as _ENG_REQ
from antcrew_engine.models._engine_fixtures import REVIEW as _ENG_REVIEW
from antcrew_engine.models._engine_fixtures import TASKS as _ENG_TASKS
from antcrew_engine.models._engine_fixtures import TEST_CODE as _ENG_TEST
from antcrew_engine.models.base import BaseLLM, Message

# ---------------------------------------------------------------------------
# Fixture responses keyed by what the system prompt contains
# ---------------------------------------------------------------------------

_CODEBASE_ANALYSIS_FIXTURE = {
    "tech_stack": ["Python 3.12", "FastAPI", "React 18", "TypeScript 5"],
    "existing_modules": ["src/auth", "src/models", "frontend/components"],
    "entry_points": ["src/main.py", "frontend/src/main.tsx"],
    "test_coverage_summary": "[Simulated] Tests exist for auth and models only",
    "what_exists": "[Simulated] Authentication system, basic CRUD endpoints, React frontend",
    "what_is_missing": "[Simulated] Billing module, PDF generation, email notifications",
    "continuation_context": "[Simulated] MVP-stage SaaS with auth and CRUD done; billing not started",
}

_PRD_FIXTURE = {
    "title": "[Simulated] Feature Module",
    "summary": "Simulated PRD for testing â€” no real LLM called.",
    "goals": ["Deliver the requested feature", "Maintain code quality"],
    "out_of_scope": ["Third-party integrations"],
    "functional_requirements": ["Core feature implementation"],
    "non_functional_requirements": ["< 200 ms P99", "Unit test coverage â‰¥ 80 %"],
    "open_questions": [],
}

_TICKETS_FIXTURE = [
    {
        "title": "[Sim] Implement core logic",
        "description": "Implement the primary business logic for the feature.",
        "priority": "high",
        "acceptance_criteria": ["All unit tests pass", "Code reviewed"],
        "dependencies": [],
    },
    {
        "title": "[Sim] Add API endpoint",
        "description": "Expose the feature via a REST endpoint.",
        "priority": "medium",
        "acceptance_criteria": ["POST /feature returns 201", "Auth required"],
        "dependencies": [],
    },
]

_CODE_ARTIFACTS_FIXTURE = [
    {
        "file_path": "src/feature/core.py",
        "description": "[Simulated] Core logic stub",
        "language": "python",
        "content": "def run():\n    \"\"\"Simulated implementation.\"\"\"\n    return {\"status\": \"ok\"}\n",
    }
]

# Single-object fixture for the "generate ONE file" phase (backend_dev phase 2).
_CODE_FILE_FIXTURE = {
    "file_path": "src/feature/core.py",
    "description": "[Simulated] Core logic stub",
    "language": "python",
    "content": "def run():\n    \"\"\"Simulated implementation.\"\"\"\n    return {\"status\": \"ok\"}\n",
}

_FRONTEND_FIXTURE = [
    {
        "file_path": "src/components/Feature.tsx",
        "description": "[Simulated] Feature component",
        "language": "typescript",
        "content": "export default function Feature() {\n  return <div>Feature</div>;\n}\n",
    }
]

# Single-object fixture for the "generate ONE file" phase (frontend_dev phase 2).
_FRONTEND_FILE_FIXTURE = {
    "file_path": "src/components/Feature.tsx",
    "description": "[Simulated] Feature component",
    "language": "typescript",
    "content": "export default function Feature() {\n  return <div>Feature</div>;\n}\n",
}

_TESTS_FIXTURE = [
    {
        "ticket_id": "TICKET-001",
        "file_path": "tests/test_core.py",
        "description": "[Simulated] Tests for core logic",
        "language": "python",
        "content": "def test_run():\n    from src.feature.core import run\n    assert run()[\"status\"] == \"ok\"\n",
        "coverage_areas": ["unit"],
    }
]

_BUG_FIXTURE = json.dumps(
    {"has_critical_bugs": False, "critical_bug_count": 0, "summary": "[Simulated] No bugs."}
)

_REVIEW_FIXTURE = {
    "verdict": "approve",
    "summary": "[Simulated] Code looks good.",
    "findings": [
        {
            "severity": "info",
            "file_path": "src/feature/core.py",
            "message": "[Simulated] Consider adding docstrings.",
            "suggestion": "Add module-level docstring.",
        }
    ],
}

_RESEARCH_FIXTURE = {
    "title": "[Simulated] Research Document",
    "topic": "Simulated research topic",
    "key_findings": [
        "Finding 1: simulated insight",
        "Finding 2: simulated implication",
    ],
    "sections": [
        {"heading": "Background", "content": "Simulated background content."},
        {"heading": "Analysis", "content": "Simulated analysis content."},
        {"heading": "Conclusions", "content": "Simulated conclusions."},
    ],
    "sources": ["[Simulated] Source A", "[Simulated] Source B"],
}

_BRIEF_FIXTURE = {
    "title": "[Simulated] Content Piece",
    "target_audience": "Software engineers",
    "tone": "professional",
    "outline": [
        "Introduction: overview of the topic",
        "Section 1: first key point",
        "Section 2: second key point",
        "Conclusion: takeaways",
    ],
}

_BODY_FIXTURE = {
    "body": (
        "## Introduction\n\n[Simulated content body.]\n\n"
        "## Section 1\n\n[Simulated section 1.]\n\n"
        "## Section 2\n\n[Simulated section 2.]\n\n"
        "## Conclusion\n\n[Simulated conclusion.]"
    ),
    "word_count": 40,
}

_DEVOPS_FIXTURE = [
    {
        "file_path": "Dockerfile",
        "description": "[Simulated] Production Docker image",
        "language": "dockerfile",
        "content": (
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "COPY requirements.txt .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "COPY . .\n"
            "EXPOSE 8000\n"
            'CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
        ),
    },
    {
        "file_path": ".github/workflows/ci.yml",
        "description": "[Simulated] GitHub Actions CI pipeline",
        "language": "yaml",
        "content": (
            "name: CI\n"
            "on:\n"
            "  push:\n"
            "    branches: [main]\n"
            "  pull_request:\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - uses: actions/setup-python@v5\n"
            "        with:\n"
            "          python-version: '3.12'\n"
            "      - run: pip install -r requirements.txt\n"
            "      - run: pytest\n"
            "      - name: Build Docker image\n"
            "        run: docker build -t app:latest .\n"
        ),
    },
    {
        "file_path": ".dockerignore",
        "description": "[Simulated] Docker build exclusions",
        "language": "text",
        "content": "__pycache__/\n*.pyc\n.env\n.git/\ntests/\n",
    },
]

_DOCS_FIXTURE = [
    {
        "file_path": "README.md",
        "title": "[Simulated] Project README",
        "doc_type": "readme",
        "format": "markdown",
        "content": (
            "# [Simulated] Project\n\n"
            "## Overview\n\n[Simulated] This project implements the requested feature.\n\n"
            "## Prerequisites\n\n- Python 3.12+\n- pip\n\n"
            "## Installation\n\n```bash\npip install -r requirements.txt\n```\n\n"
            "## Usage\n\n```bash\npython -m uvicorn main:app --reload\n```\n"
        ),
    },
    {
        "file_path": "docs/ARCHITECTURE.md",
        "title": "[Simulated] Architecture Overview",
        "doc_type": "architecture",
        "format": "markdown",
        "content": (
            "# Architecture\n\n"
            "## Components\n\n"
            "- **API Layer** â€” FastAPI REST endpoints\n"
            "- **Business Logic** â€” core feature implementation\n"
            "- **Data Layer** â€” persistence and models\n\n"
            "## Data Flow\n\nClient â†’ API â†’ Business Logic â†’ Data Layer\n"
        ),
    },
]

_JUDGE_FIXTURE = {
    "score": 8,
    "reasoning": "[Simulated] The artifact meets the requirements adequately with good coverage.",
    "strengths": ["[Simulated] Clear structure", "[Simulated] Good coverage of requirements"],
    "weaknesses": ["[Simulated] Could provide more detail in edge cases"],
}

_EDIT_FIXTURE = {
    "body": (
        "## Introduction\n\n[Simulated edited body.]\n\n"
        "## Section 1\n\n[Simulated edited section 1.]\n\n"
        "## Conclusion\n\n[Simulated edited conclusion.]"
    ),
    "word_count": 30,
    "edit_notes": ["[Simulated] Improved clarity", "[Simulated] Removed redundancy"],
}


def _pick_fixture(system: str) -> str:
    s = system.lower()

    # engine capabilities -- unique phrases, checked before generic matchers
    if 'software requirements analyst' in s:
        return _ENG_REQ
    if 'software architect' in s and 'architecture document' in s:
        return _ENG_ARCH
    if 'software project manager' in s:
        return _ENG_TASKS
    if 'list all files you need' in s:
        return _ENG_FILE_PLAN
    if 'generate the complete content for exactly one file' in s:
        return _ENG_FILE_CONTENT
    if 'writing pytest tests' in s and 'given a source file' in s:
        return _ENG_TEST
    if 'senior software engineer conducting a code review' in s:
        return _ENG_REVIEW
    # LLM-as-judge prompts â€” checked first (contain unique score/reasoning format)
    if any(phrase in s for phrase in (
        "evaluating a product requirements document",
        "evaluating development tickets",
        "evaluating ai-generated code",
        "you are a senior engineer evaluating ai-generated code",
        "evaluating an ai-generated test suite",
        "you are a senior qa engineer evaluating",
        "you are a tech lead evaluating",
        "evaluating a code review",
    )):
        return json.dumps(_JUDGE_FIXTURE)

    if "technical writer" in s or ("documentation" in s and "readme" in s):
        return json.dumps(_DOCS_FIXTURE)

    if "devops engineer" in s or "dockerfile" in s or "ci/cd" in s or (
        "infrastructure" in s and "deployment" in s
    ):
        return json.dumps(_DEVOPS_FIXTURE)

    # ----------------------------------------------------------------
    # Refine prompts â€” checked first; they share keywords with run()
    # prompts but include agent-specific phrases not present elsewhere.
    # ----------------------------------------------------------------
    if "reviewer provided feedback on the prd" in s:
        return json.dumps({**_PRD_FIXTURE, "summary": "[Simulated] Revised PRD after feedback."})
    if "reviewer provided feedback on the tickets" in s:
        return json.dumps([{**t, "title": f"[Sim/revised] {t['title']}"} for t in _TICKETS_FIXTURE])
    if "reviewer provided feedback on your code" in s:
        return json.dumps(_CODE_ARTIFACTS_FIXTURE)
    if "reviewer provided feedback on your frontend" in s or (
        "reviewer provided feedback on your code" in s and "frontend" in s
    ):
        return json.dumps(_FRONTEND_FIXTURE)
    if "reviewer provided feedback on your test suite" in s:
        return json.dumps(_TESTS_FIXTURE)
    if "reviewer of the review provided feedback" in s:
        return json.dumps(_REVIEW_FIXTURE)
    if "reviewer provided feedback on your research document" in s:
        return json.dumps(_RESEARCH_FIXTURE)
    if "reviewer provided feedback on your content brief" in s or "creative strategist" in s:
        return json.dumps(_BRIEF_FIXTURE)
    if "reviewer provided feedback on your draft" in s:
        return json.dumps(_BODY_FIXTURE)
    if "reviewer provided feedback on your edited content" in s:
        return json.dumps(_EDIT_FIXTURE)
    if "reviewer provided feedback on your documentation" in s:
        return json.dumps(_DOCS_FIXTURE)

    # ----------------------------------------------------------------
    # Normal run() prompts â€” most specific first to avoid false matches.
    # ----------------------------------------------------------------
    if "senior software architect" in s and ("existing" in s or "codebase" in s):
        return json.dumps(_CODEBASE_ANALYSIS_FIXTURE)
    if "prd" in s and ("functional_requirements" in s or "business analyst" in s):
        return json.dumps(_PRD_FIXTURE)
    if "critical bugs" in s or "critical_bug_count" in s:
        return _BUG_FIXTURE
    if "code review" in s or "verdict" in s:
        return json.dumps(_REVIEW_FIXTURE)
    if "test" in s and ("coverage" in s or "pytest" in s or "vitest" in s):
        return json.dumps(_TESTS_FIXTURE)
    # Phase-2 (file-generation) prompts — must come before the broader list fixtures.
    if "generate the complete content for exactly one frontend file" in s:
        return json.dumps(_FRONTEND_FILE_FIXTURE)
    if "generate the complete content for exactly one backend file" in s:
        return json.dumps(_CODE_FILE_FIXTURE)
    if "frontend" in s or "react" in s or "typescript" in s:
        return json.dumps(_FRONTEND_FIXTURE)
    # "code artifact" before generic "ticket" to avoid false match on BackendDev
    if "code artifact" in s or ("implement" in s and "file_path" in s):
        return json.dumps(_CODE_ARTIFACTS_FIXTURE)
    if "ticket" in s and "array" in s:
        return json.dumps(_TICKETS_FIXTURE)
    if "research" in s and "section" in s:
        return json.dumps(_RESEARCH_FIXTURE)
    if "edit_notes" in s or "editor" in s:
        return json.dumps(_EDIT_FIXTURE)
    if "copywriter" in s or ("body" in s and "word_count" in s):
        return json.dumps(_BODY_FIXTURE)
    if "outline" in s and ("content" in s or "brief" in s):
        return json.dumps(_BRIEF_FIXTURE)
    # Fallback
    return json.dumps({"result": "[Simulated] No matching fixture for this prompt."})


class SimulatedLLM(BaseLLM):
    """
    Drop-in replacement for any real LLM â€” returns fixture JSON without API calls.

    Perfect for demos, CI pipelines, and development without spending credits.

    Usage:
        team = DevTeam(model=SimulatedLLM())
        state = team.run("Build a login module")  # instant, no API calls
    """

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        system = next((m.content for m in messages if m.role == "system"), "")
        result = _pick_fixture(system)
        if self.on_token:
            self.on_token(result)
        # Approximate token counts: ~4 chars per token
        prompt = "".join(m.content for m in messages)
        self._record_usage(len(prompt) // 4, len(result) // 4)
        return result
