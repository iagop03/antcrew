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

    if s.startswith("azure:"):
        from antcrew.models.azure_openai_model import AzureOpenAIModel
        return AzureOpenAIModel(deployment=s.split(":", 1)[1])

    if s.startswith("openai:"):
        from antcrew.models.openai_model import OpenAIModel
        return OpenAIModel(s.split(":", 1)[1])

    if s.startswith("gpt") or s.startswith("o1") or s.startswith("o3"):
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

def _build_team_cfg(cfg: dict, base_dir: "Optional[Path]" = None):
    """Build a team from an already-parsed config dict.

    Internal helper — use :func:`load` for file-based configs.
    Also used recursively by ``team: auto`` and ``team: routed``.
    """
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

    # Supervisor: support `flow:` key for custom pipeline + optional `gates:`
    supervisor = None
    if "flow" in cfg:
        from antcrew.core.supervisor import Supervisor
        from antcrew.core.gates import parse_gate as _pg
        flow = [tuple(step) for step in cfg["flow"]]
        gates_raw: dict = cfg.get("gates") or {}
        gates = {name: _pg(spec) for name, spec in gates_raw.items()}
        supervisor = Supervisor(flow=flow, gates=gates if gates else None)

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

    # Cost guard (optional)
    max_cost_usd_cfg = cfg.get("max_cost_usd")
    max_cost_usd: Optional[float] = float(max_cost_usd_cfg) if max_cost_usd_cfg is not None else None

    feedback_rounds: int = int(cfg.get("feedback_rounds", 0))

    if team_type == "dev":
        from antcrew.teams.dev_team import DevTeam
        team = DevTeam(model=default_llm, integrations=integrations,
                       agents=agent_overrides, supervisor=supervisor, runner=runner,
                       max_cost_usd=max_cost_usd, feedback_rounds=feedback_rounds)
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
            sprint_size=sprint_size, max_cost_usd=max_cost_usd,
            feedback_rounds=feedback_rounds,
        )
        team.output_dir = cfg.get("output_dir") or None
        return team

    if team_type == "research":
        from antcrew.teams.research_team import ResearchTeam
        return ResearchTeam(model=default_llm, agents=agent_overrides, supervisor=supervisor,
                            max_cost_usd=max_cost_usd)

    if team_type == "content":
        from antcrew.teams.content_team import ContentTeam
        return ContentTeam(model=default_llm, agents=agent_overrides, supervisor=supervisor,
                           max_cost_usd=max_cost_usd)

    if team_type == "custom":
        steps = cfg.get("steps")
        if not steps:
            raise ValueError(
                "'team: custom' requires a non-empty 'steps:' list of agent configs.\n"
                "Each step must have at least 'name' and 'system_prompt'."
            )
        team_vars = cfg.get("vars") or {}
        validate_dag = bool(cfg.get("validate_dag", False))
        from antcrew.teams.custom_team import CustomTeam
        return CustomTeam(
            list(steps), default_llm,
            vars=team_vars,
            base_dir=base_dir,
            max_cost_usd=max_cost_usd,
            validate_dag=validate_dag,
        )

    if team_type == "auto":
        from antcrew.core.router import Router, LLMClassifier
        from antcrew.agents.direct_agent import DirectAgent
        simple_prompt = str(cfg.get("simple_prompt") or "You are a helpful assistant. Answer concisely.")
        complex_type = str(cfg.get("complex_team", "dev"))
        route_descriptions: dict = cfg.get("route_descriptions") or {
            "simple":  "Factual question, short explanation, or quick lookup — no code needed",
            "complex": "Software development task, code generation, architecture, or multi-step work",
        }
        clf_model_raw = cfg.get("classifier_model")
        classifier_llm = build_llm(str(clf_model_raw), prompt_caching=prompt_caching) if clf_model_raw else default_llm
        classifier = LLMClassifier(classifier_llm, route_descriptions)
        complex_team = _build_team_cfg({**cfg, "team": complex_type}, base_dir)
        return Router(
            classifier=classifier,
            routes={
                "simple":  DirectAgent(default_llm, system_prompt=simple_prompt),
                "complex": complex_team,
            },
            default="complex",
        )

    if team_type == "routed":
        from antcrew.core.router import Router, LLMClassifier, RuleClassifier
        from antcrew.agents.direct_agent import DirectAgent
        routes_cfg: dict = cfg.get("routes") or {}
        if not routes_cfg:
            raise ValueError("'team: routed' requires a non-empty 'routes:' block.")
        default_route = str(cfg.get("default_route", next(reversed(routes_cfg))))
        classifier_type = str(cfg.get("classifier", "llm")).lower()
        clf_model_raw = cfg.get("classifier_model")
        classifier_base_llm = build_llm(str(clf_model_raw), prompt_caching=prompt_caching) if clf_model_raw else default_llm

        if classifier_type == "rule":
            rules_raw = cfg.get("rules") or []
            classifier = RuleClassifier(
                [(str(r["pattern"]), str(r["label"])) for r in rules_raw],
                default=default_route,
            )
        else:
            route_descriptions = {
                name: str(rcfg.get("description", name))
                for name, rcfg in routes_cfg.items()
            }
            classifier = LLMClassifier(classifier_base_llm, route_descriptions, default=default_route)

        built_routes: dict = {}
        for name, rcfg in routes_cfg.items():
            rtype = str(rcfg.get("team", "direct")).lower()
            if rtype == "direct":
                sys_p = rcfg.get("system_prompt") or "You are a helpful assistant."
                built_routes[name] = DirectAgent(default_llm, system_prompt=sys_p)
            else:
                merged = {**cfg, "team": rtype, **{k: v for k, v in rcfg.items() if k != "team"}}
                built_routes[name] = _build_team_cfg(merged, base_dir)

        return Router(classifier=classifier, routes=built_routes, default=default_route)

    if team_type == "feature":
        from antcrew.agents.feature_agent import FeatureTeam
        project_dir = cfg.get("project_dir") or None
        max_tool_steps = int(cfg.get("max_tool_steps", 10))
        system_prompt_override = cfg.get("system_prompt") or None
        max_feedback_rounds = int(cfg.get("feedback_rounds", 0))
        validate_cmd_raw = cfg.get("validate_cmd")
        validate_cmd = list(validate_cmd_raw) if validate_cmd_raw else None
        validate_timeout = float(cfg.get("validate_timeout", 60.0))
        return FeatureTeam(
            llm=default_llm,
            project_dir=project_dir,
            max_tool_steps=max_tool_steps,
            max_cost_usd=max_cost_usd,
            system_prompt_override=system_prompt_override,
            max_feedback_rounds=max_feedback_rounds,
            validate_cmd=validate_cmd,
            validate_timeout=validate_timeout,
        )

    raise ValueError(
        f"Unknown team '{team_type}'. Expected: dev, fullstack, research, content, custom, feature."
    )


def load(path: str | Path):
    """Parse a team config file (.yaml or .json) and return a configured team.

    Returns one of: DevTeam | FullStackTeam | ResearchTeam | ContentTeam |
    CustomTeam | FeatureTeam | Router | …

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
    return _build_team_cfg(cfg, base_dir=path.parent)


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
    from antcrew.agents.registry import instantiate_agent, AGENT_REGISTRY

    agent = instantiate_agent(
        name, llm,
        approval_required=approval,
        response_options=options,
        channel=channel,
        agent_cfg=agent_cfg,
    )
    if agent is not None:
        return agent

    # Unknown name: allow inline template agents (system_prompt / template file).
    if "system_prompt" in agent_cfg or "template" in agent_cfg:
        from antcrew.agents.template_agent import TemplateAgent
        cfg: dict = dict(agent_cfg)
        cfg.setdefault("name", name)
        if "template" in cfg:
            from pathlib import Path as _P
            import yaml as _yaml  # type: ignore[import]
            tpl_path = _P(cfg["template"])
            file_cfg = _yaml.safe_load(tpl_path.read_text(encoding="utf-8"))
            file_cfg.update({k: v for k, v in cfg.items() if k != "template"})
            cfg = file_cfg
            cfg.setdefault("name", name)
        kwargs: dict = dict(llm=llm, approval_required=approval)
        if options:
            kwargs["response_options"] = options
        if channel:
            kwargs["channel"] = channel
        if "max_tokens" in cfg:
            kwargs["max_tokens"] = int(cfg["max_tokens"])
        if "system_prompt_suffix" in cfg:
            kwargs["system_prompt_suffix"] = str(cfg["system_prompt_suffix"])
        if "preset" in cfg:
            kwargs["preset"] = str(cfg["preset"])
        return TemplateAgent(cfg, **kwargs)

    raise ValueError(
        f"Unknown agent '{name}'. Known agents: {sorted(AGENT_REGISTRY)}\n"
        "To define a custom agent inline, add 'system_prompt:' to its config block."
    )
