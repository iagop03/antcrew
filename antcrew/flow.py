"""Flow loader and validator — define LangGraph pipelines in YAML or JSON.

Without this module you must write Python to define a custom pipeline:

    supervisor = Supervisor(flow=[
        ("business_analyst", "pm"),
        ("pm", "backend_dev"),
        ("backend_dev", "qa"),
        ("qa", "reviewer", "no_critical_bugs"),
    ])
    team = DevTeam(supervisor=supervisor)

With load_flow() you can load the same definition from a file:

    from antcrew.flow import load_flow, validate_flow

    flow = load_flow("my_flow.yaml")       # or .json
    errors = validate_flow(flow)
    if errors:
        raise ValueError("\\n".join(errors))
    team = DevTeam(supervisor=Supervisor(flow=flow))

Supported file formats
----------------------
Standalone list (flow.yaml / flow.json):
    - [business_analyst, pm]
    - [pm, backend_dev]
    - [backend_dev, qa]
    - [qa, reviewer, no_critical_bugs]  # conditional edge

    [
      ["business_analyst", "pm"],
      ["pm", "backend_dev"],
      ["qa", "reviewer", "no_critical_bugs"]
    ]

Full team config (agentteam.yaml / team.json) with a 'flow' key:
    team: dev
    model: claude
    flow:
      - [business_analyst, pm]
      - [pm, backend_dev]

Each edge is [src, dst] (unconditional) or [src, dst, condition_flag] (conditional).
Condition flags are set in agent metadata: state["metadata"]["flag"] = True.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# All agent names recognised by the built-in registry
_KNOWN_AGENTS: frozenset[str] = frozenset({
    "business_analyst",
    "pm",
    "backend_dev",
    "frontend_dev",
    "qa",
    "reviewer",
    "devops",
    "doc_writer",
    "researcher",
    "writer",
    "idea",
    "copywriter",
    "editor",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_flow(path: str | Path) -> list[tuple]:
    """Load a flow definition from a YAML or JSON file.

    The file may be:
    - A list  → treated as the flow edges directly
    - A dict  → the ``flow`` key is extracted; other keys are ignored

    Returns a list of tuples ready for ``Supervisor(flow=...)``.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError:        if the file cannot be parsed as a flow.
        ImportError:       if loading a .yaml/.yml file and PyYAML is not installed.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Flow file not found: {path}")

    raw = path.read_text(encoding="utf-8")

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    else:
        try:
            import yaml  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required to load .yaml/.yml files. "
                "Install it: pip install pyyaml"
            ) from exc
        data = yaml.safe_load(raw)

    return _parse_flow(data, source=str(path))


def validate_flow(
    flow: list[tuple],
    *,
    extra_agents: Optional[set[str]] = None,
) -> list[str]:
    """Validate a flow and return a list of error messages.

    An empty list means the flow is valid.

    Args:
        flow:          List of (src, dst) or (src, dst, condition) tuples.
        extra_agents:  Additional agent names beyond the built-in set.
                       Useful for custom agents not in the built-in registry.
    """
    known = _KNOWN_AGENTS | (extra_agents or set())
    errors: list[str] = []
    already_reported: set[str] = set()

    for i, step in enumerate(flow):
        if not isinstance(step, (tuple, list)) or len(step) < 2:
            errors.append(
                f"Edge {i}: must be [src, dst] or [src, dst, condition], got {step!r}"
            )
            continue

        for name in step[:2]:
            if not isinstance(name, str):
                errors.append(f"Edge {i}: agent name must be a string, got {name!r}")
            elif name not in known and name not in already_reported:
                errors.append(
                    f"Edge {i}: unknown agent '{name}'. "
                    f"Known agents: {sorted(known)}"
                )
                already_reported.add(name)

    return errors


def format_flow(flow: list[tuple], *, title: str = "") -> str:
    """Return a human-readable ASCII representation of a flow.

    Example:
        business_analyst  →  pm
        pm                →  backend_dev
        backend_dev       →  qa
        qa                →  reviewer    [no_critical_bugs]
        qa                →  backend_dev [has_critical_bugs]

        5 agent(s), 5 edge(s)
    """
    if not flow:
        return "  (empty flow)"

    max_src = max(len(str(e[0])) for e in flow)

    lines: list[str] = []
    if title:
        lines.append(title)
        lines.append("")

    for step in flow:
        src  = str(step[0]).ljust(max_src)
        dst  = str(step[1])
        cond = f"  [{step[2]}]" if len(step) >= 3 else ""
        lines.append(f"  {src}  →  {dst}{cond}")

    agents = {str(n) for step in flow for n in step[:2]}
    lines.append("")
    lines.append(f"  {len(agents)} agent(s), {len(flow)} edge(s)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _parse_flow(data, *, source: str = "<unknown>") -> list[tuple]:
    if isinstance(data, list):
        raw_edges = data
    elif isinstance(data, dict):
        if "flow" not in data:
            raise ValueError(
                f"No 'flow' key found in {source}. "
                "Add a 'flow:' section or pass a standalone list of edges."
            )
        raw_edges = data["flow"]
    else:
        raise ValueError(
            f"Cannot parse flow from {source}: "
            f"expected a list or dict, got {type(data).__name__}"
        )

    edges: list[tuple] = []
    for i, step in enumerate(raw_edges):
        if not isinstance(step, (list, tuple)) or len(step) < 2:
            raise ValueError(
                f"Edge {i} in {source}: must be [src, dst] or "
                f"[src, dst, condition], got {step!r}"
            )
        edges.append(tuple(step))

    return edges
