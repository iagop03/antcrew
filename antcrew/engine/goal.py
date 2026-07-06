"""Goal: what the engine is trying to achieve.

A Goal separates two things:

  DesiredProjectState — the conditions the project must satisfy.
    Each Condition has an identity (ConditionId) so the Operator and
    EventLog can reason about them by name, not by value.

  Constraints — how to get there.
    Tech choices, exclusions, and any custom requirements that Capabilities
    must respect when executing.  Constraints never appear in the
    DesiredProjectState — they shape *how* goals are reached, not *what*
    the goal is.

Condition carries no evaluation logic — that belongs to Validator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NewType

ConditionId = NewType("ConditionId", str)


@dataclass
class Condition:
    """A named, evaluable condition that the project should satisfy."""

    id: ConditionId
    description: str

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Condition):
            return self.id == other.id
        return NotImplemented

    def __repr__(self) -> str:
        return f"Condition({self.id!r})"


@dataclass(frozen=True)
class DesiredProjectState:
    """The set of conditions the project must satisfy to reach the goal."""

    conditions: frozenset[Condition]

    def gap(self, satisfied: frozenset[ConditionId]) -> frozenset[Condition]:
        """Return conditions not yet satisfied."""
        return frozenset(c for c in self.conditions if c.id not in satisfied)

    def is_satisfied_by(self, satisfied: frozenset[ConditionId]) -> bool:
        return all(c.id in satisfied for c in self.conditions)


@dataclass(frozen=True)
class Constraints:
    """How capabilities should reach the goal — not what the goal is."""

    tech_stack: tuple[str, ...] = ()     # e.g. ("Python", "FastAPI", "PostgreSQL")
    excluded:   tuple[str, ...] = ()     # e.g. ("Redis", "MongoDB")
    custom:     dict[str, Any]  = field(default_factory=dict)


@dataclass(frozen=True)
class Goal:
    description:   str
    desired_state: DesiredProjectState
    constraints:   Constraints = field(default_factory=Constraints)
