from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, TypeAdapter


def _validate_schema(schema: Any, data: Any) -> Any:
    """Validate data against schema, supporting both BaseModel subclasses and generic types."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate(data)
    return TypeAdapter(schema).validate_python(data)


def validate_agent_dag(
    agents: list,
    initial_keys: "Optional[set[str]]" = None,
    *,
    strict: bool = True,
) -> list[str]:
    """Validate that each agent's ``consumes`` keys are produced by prior agents.

    Walks the agent list in declaration order and tracks which state keys are
    available at each step.  A key is available when it is either in
    *initial_keys* or in the ``produces`` list of an agent that runs before the
    current one.

    Args:
        agents:       Ordered list of agents (objects with optional ``name``,
                      ``consumes``, and ``produces`` class/instance attributes).
        initial_keys: Keys available in the initial state (default: ``{"request"}``).
        strict:       When ``True`` (default) raise :class:`ValueError` on the
                      first violation.  When ``False`` collect all violations and
                      return them as a list of strings.

    Returns:
        List of violation messages.  Empty list means the DAG is valid.

    Raises:
        ValueError: On the first violation when ``strict=True``.

    Example::

        from antcrew import validate_agent_dag
        from antcrew.agents.business import BusinessAnalystAgent
        from antcrew.agents.pm import PMAgent
        from antcrew.models.simulated import SimulatedLLM

        llm = SimulatedLLM()
        agents = [BusinessAnalystAgent(llm), PMAgent(llm)]
        validate_agent_dag(agents)   # passes — business_analyst produces "prd" which pm consumes
    """
    available: set[str] = set(initial_keys or {"request"})
    violations: list[str] = []

    for agent in agents:
        name = getattr(agent, "name", type(agent).__name__)
        consumes: list[str] = list(getattr(agent, "consumes", []) or [])
        produces: list[str] = list(getattr(agent, "produces", []) or [])

        missing = [k for k in consumes if k not in available]
        if missing:
            msg = (
                f"Agent '{name}' consumes {sorted(missing)} but "
                f"{'that key is' if len(missing) == 1 else 'those keys are'} not "
                f"produced by any prior agent. Available: {sorted(available)}"
            )
            if strict:
                raise ValueError(msg)
            violations.append(msg)

        available.update(produces)

    return violations
