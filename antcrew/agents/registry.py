"""Central agent registry for antcrew.

Provides a single source of truth for all built-in agent types.
Used by both ``antcrew.config`` (agent instantiation) and the
``antcrew agents`` CLI command (discovery / listing).

Adding a new agent type requires updating only this file.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Mapping from config-file agent name → (module, class_name).
#: Lazy-loaded at call time to avoid circular imports.
AGENT_REGISTRY: dict[str, tuple[str, str]] = {
    "business_analyst":  ("antcrew.agents.business",         "BusinessAnalystAgent"),
    "pm":                ("antcrew.agents.pm",                "PMAgent"),
    "backend_dev":       ("antcrew.agents.backend_dev",       "BackendDevAgent"),
    "frontend_dev":      ("antcrew.agents.frontend_dev",      "FrontendDevAgent"),
    "qa":                ("antcrew.agents.qa",                "QAAgent"),
    "reviewer":          ("antcrew.agents.reviewer",          "ReviewerAgent"),
    "devops":            ("antcrew.agents.devops",            "DevOpsAgent"),
    "researcher":        ("antcrew.agents.researcher",        "ResearcherAgent"),
    "idea":              ("antcrew.agents.idea",              "IdeaAgent"),
    "copywriter":        ("antcrew.agents.copywriter",        "CopywriterAgent"),
    "editor":            ("antcrew.agents.editor",            "EditorAgent"),
    "codebase_scanner":  ("antcrew.agents.codebase_scanner",  "CodebaseScannerAgent"),
}


def get_agent_class(name: str):
    """Return the class for a registered agent name, or None if unknown."""
    entry = AGENT_REGISTRY.get(name)
    if entry is None:
        return None
    import importlib
    module, cls_name = entry
    return getattr(importlib.import_module(module), cls_name)


def instantiate_agent(
    name: str,
    llm: "BaseLLM",
    *,
    approval_required: bool = False,
    response_options=None,
    channel=None,
    agent_cfg: dict | None = None,
):
    """Instantiate a built-in agent by registry name.

    Returns the agent instance, or ``None`` if *name* is not registered
    (callers should then handle inline TemplateAgent logic themselves).
    """
    cls = get_agent_class(name)
    if cls is None:
        return None

    cfg = agent_cfg or {}
    kwargs: dict = {"llm": llm, "approval_required": approval_required}
    if response_options:
        kwargs["response_options"] = response_options
    if channel:
        kwargs["channel"] = channel
    if "max_tokens" in cfg:
        kwargs["max_tokens"] = int(cfg["max_tokens"])
    if "system_prompt_suffix" in cfg:
        kwargs["system_prompt_suffix"] = str(cfg["system_prompt_suffix"])
    if "preset" in cfg:
        kwargs["preset"] = str(cfg["preset"])
    if name == "codebase_scanner" and "ignore_dirs" in cfg:
        kwargs["extra_ignore_dirs"] = list(cfg["ignore_dirs"])

    return cls(**kwargs)
