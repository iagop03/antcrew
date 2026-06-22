"""
agentteam.yaml loader — parses config files and returns a configured team.

Minimal YAML format:
    team: dev                    # dev | research | content
    model: claude                # claude | gpt-4o | ollama:<name> | groq:<name> | simulated
    agents:                      # optional per-agent overrides (Level 2)
      backend_dev:
        model: ollama:llama3
        approval_required: true
        response_options: [approve, reject]
    channel:                     # optional
      type: telegram
      token: ${BOT_TOKEN}
      chat_id: ${CHAT_ID}

Environment variables: ${VAR_NAME} tokens are expanded from os.environ.
"""
from __future__ import annotations

import json as _json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import yaml as _yaml  # type: ignore[import]
    _HAS_YAML = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _HAS_YAML = False

from antcrew.models.base import BaseLLM


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} tokens in strings."""
    if isinstance(value, str):
        return re.sub(
            r"\$\{([^}]+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def build_llm(model_str: str, *, prompt_caching: bool = False) -> BaseLLM:
    """Parse 'claude', 'gpt-4o', 'ollama:llama3', 'groq:llama3-70b', 'simulated'."""
    s = model_str.strip().lower()

    if s == "simulated":
        from antcrew.models.simulated import SimulatedLLM
        return SimulatedLLM()

    if s.startswith("ollama:"):
        from antcrew.models.ollama_model import OllamaModel
        return OllamaModel(s.split(":", 1)[1])

    if s.startswith("groq:"):
        from antcrew.models.groq_model import GroqModel
        return GroqModel(s.split(":", 1)[1])

    if s.startswith("gpt"):
        from antcrew.models.openai_model import OpenAIModel
        return OpenAIModel(s)

    if s.startswith("gemini"):
        from antcrew.models.gemini_model import GeminiModel
        return GeminiModel(s)

    if s in ("gemini",):
        from antcrew.models.gemini_model import GeminiModel
        return GeminiModel()

    if s in ("claude", "anthropic") or s.startswith("claude-"):
        from antcrew.models.anthropic_model import AnthropicModel
        model_id = None if s in ("claude", "anthropic") else s
        return AnthropicModel(
            **({"model": model_id} if model_id else {}),
            prompt_caching=prompt_caching,
        )

    raise ValueError(
        f"Unknown model '{model_str}'. "
        "Expected: claude, gpt-4o, gemini, ollama:<name>, groq:<name>, simulated."
    )


def _build_channel(cfg: dict):
    channel_type = cfg.get("type", "").lower()
    if channel_type == "telegram":
        from antcrew.integrations.telegram.integration import TelegramChannel
        return TelegramChannel(
            token=cfg["token"],
            chat_id=cfg["chat_id"],
        )
    if channel_type == "console":
        from antcrew.integrations.console import ConsoleChannel
        return ConsoleChannel()
    if channel_type == "slack":
        from antcrew.integrations.slack import SlackChannel
        return SlackChannel(
            bot_token=cfg["bot_token"],
            app_token=cfg["app_token"],
            channel_id=cfg["channel_id"],
        )
    raise ValueError(
        f"Unknown channel type '{channel_type}'. Supported: telegram, console, slack."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(path: str | Path):
    """Parse a team config file (.yaml or .json) and return a configured team.

    Returns one of: DevTeam | FullStackTeam | ResearchTeam | ContentTeam

    JSON is supported natively (no extra dependencies).
    YAML requires PyYAML (``pip install pyyaml``).
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        cfg: dict = _expand_env(_json.loads(raw))
    else:
        if not _HAS_YAML:
            raise ImportError(
                "PyYAML is required to load YAML files. "
                "Install it: pip install pyyaml"
            )
        cfg: dict = _expand_env(_yaml.safe_load(raw))

    team_type = cfg.get("team", "dev").lower()
    prompt_caching = bool(cfg.get("prompt_caching", False))
    default_llm = build_llm(cfg.get("model", "claude"), prompt_caching=prompt_caching)

    # Cache: attach FileLLMCache when "cache:" key is present
    if "cache" in cfg:
        cache_path = Path(str(cfg["cache"])).expanduser()
        from antcrew.models.cache import FileLLMCache
        default_llm.with_cache(FileLLMCache(cache_path))

    # Channels: support both `channel:` (singular) and `channels:` (list)
    integrations: list = []
    if "channel" in cfg:
        integrations.append(_build_channel(cfg["channel"]))
    for ch_cfg in cfg.get("channels") or []:
        integrations.append(_build_channel(ch_cfg))
    # Use first channel for per-agent channel assignment
    channel = integrations[0] if integrations else None

    # Supervisor: support `flow:` key for custom pipeline
    supervisor = None
    if "flow" in cfg:
        from antcrew.core.supervisor import Supervisor
        flow = [tuple(step) for step in cfg["flow"]]
        supervisor = Supervisor(flow=flow)

    # Per-agent overrides
    agent_overrides: dict = {}
    for agent_name, agent_cfg in (cfg.get("agents") or {}).items():
        agent_llm = build_llm(
            agent_cfg["model"],
            prompt_caching=prompt_caching,
        ) if "model" in agent_cfg else default_llm
        approval = bool(agent_cfg.get("approval_required", False))
        options = agent_cfg.get("response_options")
        agent_overrides[agent_name] = _resolve_agent(
            agent_name, agent_llm, approval, options, channel, agent_cfg
        )

    # Runner (optional — only applies to dev / fullstack teams)
    runner = None
    if "runner" in cfg:
        runner = build_runner(cfg["runner"])

    if team_type == "dev":
        from antcrew.teams.dev_team import DevTeam
        team = DevTeam(model=default_llm, integrations=integrations,
                       agents=agent_overrides, supervisor=supervisor, runner=runner)
        team.output_dir = cfg.get("output_dir") or None
        return team

    if team_type == "fullstack":
        from antcrew.teams.fullstack_team import FullStackTeam
        project_dir = cfg.get("project_dir") or None
        project_dirs = cfg.get("project_dirs") or None   # dict label→path
        sprint_size = int(cfg.get("sprint_size", 4))
        team = FullStackTeam(
            model=default_llm, integrations=integrations,
            agents=agent_overrides, supervisor=supervisor, runner=runner,
            project_dir=project_dir, project_dirs=project_dirs,
            sprint_size=sprint_size,
        )
        team.output_dir = cfg.get("output_dir") or None
        return team

    if team_type == "research":
        from antcrew.teams.research_team import ResearchTeam
        return ResearchTeam(model=default_llm, agents=agent_overrides, supervisor=supervisor)

    if team_type == "content":
        from antcrew.teams.content_team import ContentTeam
        return ContentTeam(model=default_llm, agents=agent_overrides, supervisor=supervisor)

    raise ValueError(
        f"Unknown team '{team_type}'. Expected: dev, fullstack, research, content."
    )


def build_runner(cfg: dict):
    """Parse a ``runner:`` block and return a SandboxRunner instance."""
    runner_type = str(cfg.get("type", "local")).lower()

    if runner_type == "local":
        from antcrew.sandbox.runner import LocalRunner
        return LocalRunner(timeout=int(cfg.get("timeout", 60)))

    if runner_type == "docker":
        from antcrew.sandbox.runner import DockerRunner
        reqs = cfg.get("requirements")
        cpus_raw = cfg.get("cpus")
        return DockerRunner(
            image=cfg.get("image", "python:3.12-slim"),
            requirements=list(reqs) if reqs is not None else None,
            timeout=int(cfg.get("timeout", 120)),
            network=str(cfg.get("network", "none")),
            memory=str(cfg.get("memory", "512m")) if cfg.get("memory") is not None else "512m",
            cpus=str(cpus_raw) if cpus_raw is not None else "1.0",
        )

    raise ValueError(
        f"Unknown runner type '{runner_type}'. Expected: local, docker."
    )


@dataclass
class TeamContext:
    """Result of :func:`load_context` — team with optional attached Project.

    Attributes:
        team:    Configured team instance (DevTeam, ResearchTeam, etc.).
        project: :class:`~antcrew.project.Project` instance when ``project:``
                 is set in the config file; ``None`` otherwise.
    """

    team: Any
    project: Optional[Any] = None  # Project | None


def load_context(path: str | Path) -> TeamContext:
    """Like :func:`load` but also handles ``project:`` and returns a :class:`TeamContext`.

    When the config file contains a ``project:`` key the path is used to load
    (or create) a persistent :class:`~antcrew.project.Project` so that
    consecutive runs accumulate state automatically.

    ``cache:`` is handled by the underlying :func:`load` call — the team's LLM
    will already have a :class:`~antcrew.models.cache.FileLLMCache` attached.

    Example config (YAML or JSON)::

        team: dev
        model: claude
        cache: ~/.antcrew/cache.db      # optional — persist LLM responses
        project: ./auth-service.json    # optional — persist project state

    Args:
        path: Path to a ``.yaml`` or ``.json`` config file.

    Returns:
        :class:`TeamContext` with ``team`` always set and ``project`` set when
        the config file contains a ``project:`` key.
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        cfg: dict = _expand_env(_json.loads(raw))
    else:
        if not _HAS_YAML:
            raise ImportError(
                "PyYAML is required to load YAML files. "
                "Install it: pip install pyyaml"
            )
        cfg = _expand_env(_yaml.safe_load(raw))

    team = load(path)  # handles model, cache, runner, channels, agents …

    project = None
    if "project" in cfg:
        from antcrew.project import Project

        proj_path = Path(str(cfg["project"])).expanduser()
        team_spec = {"type": "config", "path": str(path.resolve())}

        if proj_path.exists():
            project = Project.load(proj_path, team=team)
        else:
            project = Project(team, name=proj_path.stem, path=proj_path)

        project._team_spec = team_spec

    return TeamContext(team=team, project=project)


def _resolve_agent(name: str, llm: BaseLLM, approval: bool, options, channel, agent_cfg: dict):
    """Instantiate the right agent class by name."""
    from antcrew.agents.business import BusinessAnalystAgent
    from antcrew.agents.pm import PMAgent
    from antcrew.agents.backend_dev import BackendDevAgent
    from antcrew.agents.frontend_dev import FrontendDevAgent
    from antcrew.agents.qa import QAAgent
    from antcrew.agents.reviewer import ReviewerAgent
    from antcrew.agents.devops import DevOpsAgent
    from antcrew.agents.researcher import ResearcherAgent
    from antcrew.agents.idea import IdeaAgent
    from antcrew.agents.copywriter import CopywriterAgent
    from antcrew.agents.editor import EditorAgent
    from antcrew.agents.codebase_scanner import CodebaseScannerAgent

    registry = {
        "business_analyst":  BusinessAnalystAgent,
        "pm":                PMAgent,
        "backend_dev":       BackendDevAgent,
        "frontend_dev":      FrontendDevAgent,
        "qa":                QAAgent,
        "reviewer":          ReviewerAgent,
        "devops":            DevOpsAgent,
        "researcher":        ResearcherAgent,
        "idea":              IdeaAgent,
        "copywriter":        CopywriterAgent,
        "editor":            EditorAgent,
        "codebase_scanner":  CodebaseScannerAgent,
    }
    cls = registry.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown agent '{name}'. Known agents: {list(registry)}"
        )
    kwargs: dict = dict(llm=llm, approval_required=approval)
    if options:
        kwargs["response_options"] = options
    if channel:
        kwargs["channel"] = channel

    # Per-agent YAML knobs forwarded to BaseAgent.__init__
    if "max_tokens" in agent_cfg:
        kwargs["max_tokens"] = int(agent_cfg["max_tokens"])
    if "system_prompt_suffix" in agent_cfg:
        kwargs["system_prompt_suffix"] = str(agent_cfg["system_prompt_suffix"])

    # codebase_scanner-specific extra: ignore_dirs
    if name == "codebase_scanner" and "ignore_dirs" in agent_cfg:
        kwargs["extra_ignore_dirs"] = list(agent_cfg["ignore_dirs"])

    return cls(**kwargs)
