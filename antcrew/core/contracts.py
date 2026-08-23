"""Artifact-level contracts for AntCrew agents.

Declares what artifact types an agent consumes (reads) and produces (writes).
The Supervisor validates these declarations at ``build()`` time — before any LLM
call is made — so contract violations surface as ``ContractViolationError`` with
a clear message instead of a downstream KeyError or None-access.

Usage::

    from antcrew.core.contracts import agent_contract
    from antcrew.core.artifacts import PRD, CodeArtifact

    @agent_contract(consumes=PRD, produces=CodeArtifact)
    class BackendDevAgent(BaseAgent):
        ...

    @agent_contract(produces=PRD)          # only declares what it produces
    class BusinessAnalystAgent(BaseAgent):
        ...

Multiple types::

    @agent_contract(consumes=(PRD, APISpec), produces=CodeArtifact)
    class BackendDevAgent(BaseAgent):
        ...
"""
from __future__ import annotations

from typing import Sequence, Type, Union


class ContractViolationError(ValueError):
    """Raised by Supervisor.build() when artifact contracts are not satisfied."""


ArtifactType = Type[object]


def agent_contract(
    *,
    consumes: Union[ArtifactType, Sequence[ArtifactType], None] = None,
    produces: Union[ArtifactType, Sequence[ArtifactType], None] = None,
):
    """Class decorator that declares an agent's artifact contract.

    Args:
        consumes: Artifact type(s) the agent reads from TeamState.
                  At least one predecessor in the flow must produce each type.
        produces: Artifact type(s) the agent writes to TeamState.
                  Used by downstream agents' ``consumes`` checks.

    Raises:
        ContractViolationError: At Supervisor.build() time if a consumed type
            has no producing predecessor in the declared flow.
    """
    _consumes = _normalise(consumes)
    _produces = _normalise(produces)

    def decorator(cls):
        cls._artifact_consumes = _consumes
        cls._artifact_produces = _produces
        return cls

    return decorator


def _normalise(types) -> tuple:
    if types is None:
        return ()
    if isinstance(types, type):
        return (types,)
    return tuple(types)


def validate_contracts(
    flow: list,          # list of (src, dst, condition) triples
    agents: dict,        # name → agent instance
) -> None:
    """Validate artifact contracts for a compiled flow.

    Called by Supervisor.build(). Raises ContractViolationError with a
    descriptive message on the first violation found.

    For each agent with ``_artifact_consumes``, at least one predecessor in
    the flow must have ``_artifact_produces`` containing each consumed type.
    Agents without the decorator attribute are treated as producing/consuming
    nothing (backward-compatible — existing agents are unaffected).
    """
    # Map each agent name → set of types it produces
    produces_map: dict[str, frozenset] = {
        name: frozenset(getattr(agent, "_artifact_produces", ()))
        for name, agent in agents.items()
    }

    # All types produced anywhere in the pipeline (for error messages)
    all_produced: set = set()
    for types in produces_map.values():
        all_produced.update(types)

    # For each agent, collect its direct predecessors in the flow
    predecessors: dict[str, set[str]] = {name: set() for name in agents}
    for src, dst, _ in flow:
        if dst in predecessors:
            predecessors[dst].add(src)

    # Transitively expand: also include predecessors-of-predecessors
    def _all_ancestors(name: str, visited: set | None = None) -> set[str]:
        visited = visited or set()
        for pred in predecessors.get(name, ()):
            if pred not in visited:
                visited.add(pred)
                _all_ancestors(pred, visited)
        return visited

    for name, agent in agents.items():
        required = getattr(agent, "_artifact_consumes", ())
        if not required:
            continue
        ancestors = _all_ancestors(name)
        produced_by_ancestors: set = set()
        for anc in ancestors:
            produced_by_ancestors.update(produces_map.get(anc, ()))

        for artifact_type in required:
            if artifact_type not in produced_by_ancestors:
                producer_names = [
                    n for n, types in produces_map.items()
                    if artifact_type in types
                ]
                raise ContractViolationError(
                    f"Artifact contract violation in agent '{name}': "
                    f"consumes {artifact_type.__name__} but no ancestor in the flow produces it. "
                    f"Agents that produce {artifact_type.__name__}: "
                    f"{producer_names if producer_names else '(none — add @agent_contract(produces=...) to a predecessor)'}. "
                    f"All types produced in this flow: "
                    f"{sorted(t.__name__ for t in all_produced) or '(none)'}"
                )
