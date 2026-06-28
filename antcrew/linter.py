"""Static linter for agentteam.yaml / flow.json configs.

Checks performed (no LLM calls, no API keys required):

    ERROR level
    -----------
    - Unknown team name
    - Unknown model string (doesn't match any known prefix)
    - Per-agent unknown model string
    - Flow cycle detection
    - Channel type unknown
    - Runner type unknown
    - max_cost_usd not a positive number

    WARNING level
    -------------
    - Unresolved ${VAR} tokens after env expansion
    - Per-agent unknown agent name (agent not in built-in registry)
    - Per-agent unknown preset name
    - flow: edges referencing unknown agent names
    - Missing recommended keys (team, model) — defaults will be used
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Severity = Literal["error", "warning", "info"]


class LintError:
    __slots__ = ("severity", "message", "path")

    def __init__(self, severity: Severity, message: str, path: str = "") -> None:
        self.severity = severity
        self.message = message
        self.path = path  # dot-notation key path, e.g. "agents.backend_dev.model"

    def __repr__(self) -> str:
        loc = f"[{self.path}] " if self.path else ""
        return f"{self.severity.upper()}: {loc}{self.message}"


# ---------------------------------------------------------------------------
# Known values
# ---------------------------------------------------------------------------

_KNOWN_TEAMS = frozenset({"dev", "fullstack", "research", "content"})

_KNOWN_MODEL_PREFIXES = (
    "claude", "anthropic",
    "gpt", "o1", "o3",
    "openai:", "azure:",
    "gemini",
    "ollama:",
    "groq:",
    "simulated",
    "deepseek",
    "mistral",
)

_KNOWN_CHANNEL_TYPES = frozenset({"telegram", "console", "slack"})
_KNOWN_RUNNER_TYPES = frozenset({"local", "docker"})

_KNOWN_AGENT_NAMES = frozenset({
    "business_analyst", "pm", "backend_dev", "frontend_dev",
    "qa", "reviewer", "devops", "doc_writer",
    "codebase_scanner", "sprint_planner",
    "researcher", "writer", "editor",
    "idea", "copywriter",
})

_KNOWN_PRESETS = frozenset({"concise", "strict", "verbose", "careful"})

_UNRESOLVED_RE = re.compile(r"\$\{[^}]+\}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lint_config(path: str | Path) -> list[LintError]:
    """Lint a YAML or JSON team config and return a list of LintError objects.

    An empty list means the config is clean.  Errors must be fixed before the
    config will load; warnings are advisory.
    """
    path = Path(path)
    if not path.exists():
        return [LintError("error", f"File not found: {path}")]

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [LintError("error", f"Cannot read file: {exc}")]

    # Parse without env expansion first (so we can detect unresolved vars)
    try:
        if path.suffix.lower() == ".json":
            cfg_raw: dict = json.loads(raw)
        else:
            if not _HAS_YAML:
                return [LintError("error", "PyYAML not installed; cannot lint .yaml files.")]
            cfg_raw = _yaml.safe_load(raw) or {}
    except Exception as exc:
        return [LintError("error", f"Parse error: {exc}")]

    errors: list[LintError] = []

    # Expand env vars (for value checks), but track unresolved tokens in raw
    from antcrew.config import _expand_env
    cfg: dict = _expand_env(cfg_raw)

    # ── Detect unresolved ${VAR} tokens ─────────────────────────────────────
    _scan_unresolved(cfg_raw, "", errors)

    # ── team ────────────────────────────────────────────────────────────────
    team = str(cfg.get("team", "dev")).lower()
    if "team" not in cfg:
        errors.append(LintError("info", "No 'team' key — defaulting to 'dev'"))
    elif team not in _KNOWN_TEAMS:
        errors.append(LintError(
            "error",
            f"Unknown team '{team}'. Valid teams: {sorted(_KNOWN_TEAMS)}",
            "team",
        ))

    # ── model ────────────────────────────────────────────────────────────────
    model = str(cfg.get("model", "claude"))
    if "model" not in cfg:
        errors.append(LintError("info", "No 'model' key — defaulting to 'claude'"))
    else:
        _check_model(model, "model", errors)

    # ── max_cost_usd ─────────────────────────────────────────────────────────
    if "max_cost_usd" in cfg:
        try:
            v = float(cfg["max_cost_usd"])
            if v <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(LintError(
                "error",
                f"max_cost_usd must be a positive number, got {cfg['max_cost_usd']!r}",
                "max_cost_usd",
            ))

    # ── agents ────────────────────────────────────────────────────────────────
    for agent_name, agent_cfg in (cfg.get("agents") or {}).items():
        base = f"agents.{agent_name}"
        if not isinstance(agent_cfg, dict):
            errors.append(LintError("error", "Agent config must be a dict", base))
            continue
        if agent_name not in _KNOWN_AGENT_NAMES:
            errors.append(LintError(
                "warning",
                f"Unknown agent name '{agent_name}'. "
                f"Known agents: {sorted(_KNOWN_AGENT_NAMES)}",
                base,
            ))
        if "model" in agent_cfg:
            _check_model(str(agent_cfg["model"]), f"{base}.model", errors)
        if "preset" in agent_cfg:
            p = str(agent_cfg["preset"]).lower().strip()
            if p not in _KNOWN_PRESETS:
                errors.append(LintError(
                    "warning",
                    f"Unknown preset '{agent_cfg['preset']}'. "
                    f"Built-in presets: {sorted(_KNOWN_PRESETS)}",
                    f"{base}.preset",
                ))
        if "approval_required" in agent_cfg:
            if not isinstance(agent_cfg["approval_required"], bool):
                errors.append(LintError(
                    "warning",
                    "approval_required should be a boolean (true/false)",
                    f"{base}.approval_required",
                ))

    # ── flow ─────────────────────────────────────────────────────────────────
    if "flow" in cfg:
        flow_raw = cfg["flow"]
        if not isinstance(flow_raw, list):
            errors.append(LintError("error", "flow must be a list of edges", "flow"))
        else:
            flow = [tuple(e) for e in flow_raw if isinstance(e, (list, tuple))]
            # Delegate to existing validate_flow for unknown-agent warnings
            from antcrew.flow import validate_flow
            for msg in validate_flow(flow):
                errors.append(LintError("warning", msg, "flow"))
            # Cycle detection
            cycle = _detect_cycle(flow)
            if cycle:
                errors.append(LintError(
                    "error",
                    f"Cycle detected in flow: {' → '.join(cycle)}",
                    "flow",
                ))

    # ── channel(s) ───────────────────────────────────────────────────────────
    channels = []
    if "channel" in cfg:
        channels.append(("channel", cfg["channel"]))
    for i, ch in enumerate(cfg.get("channels") or []):
        channels.append((f"channels[{i}]", ch))

    for ch_path, ch_cfg in channels:
        if not isinstance(ch_cfg, dict):
            errors.append(LintError("error", "Channel config must be a dict", ch_path))
            continue
        ch_type = str(ch_cfg.get("type", "")).lower()
        if not ch_type:
            errors.append(LintError("error", "Channel missing 'type' key", ch_path))
        elif ch_type not in _KNOWN_CHANNEL_TYPES:
            errors.append(LintError(
                "error",
                f"Unknown channel type '{ch_type}'. "
                f"Supported: {sorted(_KNOWN_CHANNEL_TYPES)}",
                f"{ch_path}.type",
            ))

    # ── runner ───────────────────────────────────────────────────────────────
    if "runner" in cfg:
        runner_cfg = cfg["runner"]
        if not isinstance(runner_cfg, dict):
            errors.append(LintError("error", "runner must be a dict", "runner"))
        else:
            r_type = str(runner_cfg.get("type", "local")).lower()
            if r_type not in _KNOWN_RUNNER_TYPES:
                errors.append(LintError(
                    "error",
                    f"Unknown runner type '{r_type}'. "
                    f"Supported: {sorted(_KNOWN_RUNNER_TYPES)}",
                    "runner.type",
                ))

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_model(model: str, path: str, errors: list[LintError]) -> None:
    s = model.lower().strip()
    if not any(s.startswith(p) for p in _KNOWN_MODEL_PREFIXES):
        errors.append(LintError(
            "error",
            f"Unrecognised model '{model}'. "
            "Expected prefix: claude, gpt-*, o1, o3, gemini, ollama:<name>, "
            "groq:<name>, azure:<deployment>, openai:<model>, simulated.",
            path,
        ))


def _scan_unresolved(obj, path: str, errors: list[LintError]) -> None:
    if isinstance(obj, str):
        if _UNRESOLVED_RE.search(obj):
            errors.append(LintError(
                "warning",
                f"Unresolved env var token: {obj!r}",
                path,
            ))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _scan_unresolved(v, f"{path}.{k}" if path else k, errors)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_unresolved(v, f"{path}[{i}]", errors)


def _detect_cycle(flow: list[tuple]) -> list[str] | None:
    """Return the cycle path as a list of node names, or None if acyclic."""
    adj: dict[str, list[str]] = {}
    for edge in flow:
        if len(edge) >= 2:
            src, dst = str(edge[0]), str(edge[1])
            adj.setdefault(src, []).append(dst)

    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> bool:
        colour[node] = GRAY
        stack.append(node)
        for nxt in adj.get(node, []):
            if colour.get(nxt) == GRAY:
                stack.append(nxt)
                return True
            if colour.get(nxt) != BLACK:
                if dfs(nxt):
                    return True
        colour[node] = BLACK
        stack.pop()
        return False

    all_nodes = {str(n) for edge in flow for n in list(edge)[:2]}
    for node in sorted(all_nodes):
        if colour.get(node, WHITE) == WHITE:
            if dfs(node):
                return list(stack)
    return None
