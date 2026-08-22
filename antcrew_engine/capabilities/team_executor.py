"""TeamExecutor: bridge between the capability engine and legacy antcrew Teams/Agents.

Any antcrew Team or Agent that has a `.run(input: str) -> dict` method can be
wrapped as an engine Executor.  The engine stays decoupled — it never imports
Team classes directly.

Usage
-----
    from antcrew.teams.dev_team import DevTeam
    from antcrew_engine.capabilities.team_executor import TeamExecutor
    from antcrew_engine.engine import CapabilityDescriptor, ConditionId

    descriptor = CapabilityDescriptor(
        name="dev_team",
        description="Runs the legacy DevTeam pipeline.",
        needs=frozenset([ConditionId("requirements_exists")]),
        produces=frozenset([ConditionId("implementation_exists")]),
        cost=5.0,
    )
    executor = TeamExecutor(DevTeam(model=llm), descriptor, output_artifact_id="dev_team_output")

Mapping
-------
The team receives ``goal.description`` as its input string.  Its output is
stored verbatim (if dict) or wrapped in ``{"output": str(...)}`` as a GENERIC
artifact under *output_artifact_id*.

For more control, subclass TeamExecutor and override ``_extract_artifacts`` to
map specific fields from the team's output dict into typed engine Artifacts.
"""
from __future__ import annotations

from antcrew_engine.engine import (
    Artifact,
    ArtifactDelta,
    ArtifactId,
    ArtifactKind,
    CapabilityDescriptor,
    CapabilityResult,
)

from .base import BaseExecutor


class TeamExecutor(BaseExecutor):
    """Wraps a legacy antcrew Team or Agent as an engine Executor.

    Parameters
    ----------
    team:
        Any object with a ``run(input: str) -> dict`` method.
    descriptor:
        The CapabilityDescriptor declaring this executor's contract.
    output_artifact_id:
        ArtifactId under which the team's output will be stored.
    output_kind:
        ArtifactKind for the output artifact (default: GENERIC).
    input_builder:
        Optional callable ``(store, goal) -> str`` that constructs the string
        input passed to ``team.run()``.  Defaults to ``goal.description``.
    """

    def __init__(
        self,
        team,
        descriptor:        CapabilityDescriptor,
        output_artifact_id: str,
        output_kind:       ArtifactKind = ArtifactKind.GENERIC,
        input_builder=None,
    ) -> None:
        super().__init__(llm=None)
        self._team              = team
        self.descriptor         = descriptor
        self._output_id         = ArtifactId(output_artifact_id)
        self._output_kind       = output_kind
        self._input_builder     = input_builder or (lambda store, goal: goal.description)

    def _run(self, store, goal) -> CapabilityResult:
        input_text = self._input_builder(store, goal)
        raw = self._team.run(input_text)
        artifacts = self._extract_artifacts(raw, store, goal)
        return CapabilityResult(delta=ArtifactDelta(created=tuple(artifacts)))

    def _extract_artifacts(self, team_output, store, goal) -> list[Artifact]:
        """Convert team output to engine Artifacts.

        Override in subclasses for richer mapping.
        Default: store the whole output as one GENERIC artifact.
        """
        content = (
            team_output
            if isinstance(team_output, dict)
            else {"output": str(team_output)}
        )
        return [Artifact(id=self._output_id, kind=self._output_kind, content=content)]
