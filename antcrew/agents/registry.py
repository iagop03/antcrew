"""Central agent registry for antcrew.

Provides a single source of truth for all built-in agent types.
Used by both ``antcrew.config`` (agent instantiation) and the
``antcrew agents`` CLI command (discovery / listing).

Adding a new agent type requires updating only this file.

Third-party plugins can register additional agents via setuptools
entry points using the group ``antcrew.agents``::

    # in the plugin's pyproject.toml:
    [project.entry-points."antcrew.agents"]
    my_agent = "my_package.agents:MyAgent"

Installed plugins are merged into AGENT_REGISTRY at import time.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcrew.models.base import BaseLLM

log = logging.getLogger(__name__)

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
    "sprint_planner":    ("antcrew.agents.sprint_planner",    "SprintPlannerAgent"),
    "doc_writer":        ("antcrew.agents.doc_writer",        "DocWriterAgent"),
    "feature":           ("antcrew.agents.feature_agent",     "FeatureAgent"),
}


def _load_plugins() -> None:
    """Merge agents registered via the ``antcrew.agents`` entry-point group.

    Each entry point must have the form ``name = module.path:ClassName``.
    Built-in names are never overwritten — plugins cannot shadow core agents.
    Errors in individual plugins are logged and skipped.
    """
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="antcrew.agents")
    except Exception:
        return

    for ep in eps:
        if ep.name in AGENT_REGISTRY:
            log.debug("plugin_agent_skipped name=%s (built-in exists)", ep.name)
            continue
        if ":" not in ep.value:
            log.warning("plugin_agent_bad_value name=%s value=%r", ep.name, ep.value)
            continue
        module, cls_name = ep.value.rsplit(":", 1)
        AGENT_REGISTRY[ep.name] = (module, cls_name)
        log.debug("plugin_agent_registered name=%s module=%s cls=%s", ep.name, module, cls_name)


_load_plugins()


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
    if "max_cost_usd" in cfg:
        kwargs["max_cost_usd"] = float(cfg["max_cost_usd"])
    if name == "codebase_scanner" and "ignore_dirs" in cfg:
        kwargs["extra_ignore_dirs"] = list(cfg["ignore_dirs"])
    if name == "feature":
        if "project_dir" in cfg:
            kwargs["project_dir"] = str(cfg["project_dir"])
        if "max_tool_steps" in cfg:
            kwargs["max_tool_steps"] = int(cfg["max_tool_steps"])
        if "system_prompt" in cfg:
            kwargs["system_prompt_override"] = str(cfg["system_prompt"])

    return cls(**kwargs)
