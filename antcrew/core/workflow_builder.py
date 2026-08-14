"""
WorkflowBuilder — fluent API that compiles to a Supervisor without exposing LangGraph.

Users write:
    from antcrew import WorkflowBuilder

    result = (WorkflowBuilder()
        .step("spec",   SpecGenerator)
        .step("impl",   BackendDevAgent,   after="spec")
        .step("review", ReviewerAgent,     after="impl")
        .step("test",   QAAgent,           after="impl")
        .step("gate",   RequirementsGate,  after=["review", "test"],
              when="no_critical_bugs")
        .parallel("coding", [BackendDevAgent, FrontendDevAgent], after="spec")
        .build(llm)
        .run("Build FastAPI auth service"))

Internally this converts to Supervisor flow edges and a compiled LangGraph app.
LangGraph is never mentioned in user-facing code.
"""
from __future__ import annotations


class Workflow:
    """Compiled workflow returned by :meth:`WorkflowBuilder.build`.

    Wraps the underlying LangGraph app so users never import from LangGraph.
    """

    def __init__(self, app, llm) -> None:
        self._app = app
        self._llm = llm

    def run(self, request: str, *, thread_id: str | None = None) -> dict:
        """Execute the workflow and return the final state dict."""
        import uuid

        from antcrew.core.state import TeamState

        tid = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": tid}}
        initial: TeamState = {"request": request, "messages": [], "metadata": {}}
        result = self._app.invoke(initial, config)
        return dict(result)

    def stream(self, request: str, *, thread_id: str | None = None):
        """Yield (node_name, partial_state) tuples as each step completes."""
        import uuid

        from antcrew.core.state import TeamState

        tid = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": tid}}
        initial: TeamState = {"request": request, "messages": [], "metadata": {}}
        yield from self._app.stream(initial, config)


class WorkflowBuilder:
    """Fluent builder that converts steps into a Supervisor-based LangGraph workflow.

    Steps are added via :meth:`step` (single agent) or :meth:`parallel` (concurrent
    agents).  :meth:`build` instantiates all agents, compiles the graph, and returns
    a :class:`Workflow` ready to :meth:`~Workflow.run`.

    Edge semantics:
      - ``after=None`` or no ``after``:  step is an entry node (wired from START).
      - ``after="name"`` or ``after=["a","b"]``:  step depends on named step(s).
      - ``when="key"``:  conditional edge — the previous step must set
        ``state["metadata"]["key"] = True`` for this edge to fire.

    Example::

        from antcrew import WorkflowBuilder
        from antcrew.agents import PMAgent, BackendDevAgent, QAAgent

        result = (WorkflowBuilder()
            .step("pm",   PMAgent)
            .step("impl", BackendDevAgent, after="pm")
            .step("qa",   QAAgent,         after="impl")
            .build(llm)
            .run("Add OAuth2 login"))
    """

    def __init__(self) -> None:
        self._steps: list[dict] = []

    # ------------------------------------------------------------------
    # Step registration
    # ------------------------------------------------------------------

    def step(
        self,
        name: str,
        agent_class: type,
        *,
        after: "str | list[str] | None" = None,
        when: "str | None" = None,
        **agent_kwargs,
    ) -> "WorkflowBuilder":
        """Add a single-agent step.

        Args:
            name:         Unique node name used in the flow graph.
            agent_class:  A :class:`~antcrew.core.agent.BaseAgent` subclass.
            after:        Name(s) of steps this step depends on.
            when:         Condition key in ``state["metadata"]``; if set, this edge
                          is conditional.
            **agent_kwargs: Extra kwargs forwarded to *agent_class.__init__*.
        """
        deps = _normalise_deps(after)
        self._steps.append({
            "name":         name,
            "agent_class":  agent_class,
            "deps":         deps,
            "when":         when,
            "parallel":     False,
            "agent_kwargs": agent_kwargs,
        })
        return self

    def parallel(
        self,
        name: str,
        agent_classes: list[type],
        *,
        after: "str | list[str] | None" = None,
        **agent_kwargs,
    ) -> "WorkflowBuilder":
        """Add a parallel group — all agents run concurrently and their state is merged.

        Args:
            name:           Unique node name for this group.
            agent_classes:  List of :class:`~antcrew.core.agent.BaseAgent` subclasses.
            after:          Name(s) of steps this group depends on.
            **agent_kwargs: Forwarded to each agent's ``__init__``.
        """
        deps = _normalise_deps(after)
        self._steps.append({
            "name":          name,
            "agent_classes": agent_classes,
            "deps":          deps,
            "when":          None,
            "parallel":      True,
            "agent_kwargs":  agent_kwargs,
        })
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, llm, *, checkpointer=None) -> Workflow:
        """Instantiate agents, compile the flow, and return a :class:`Workflow`.

        Args:
            llm:          :class:`~antcrew.models.base.BaseLLM` instance shared by
                          all agents (each gets its own shallow copy for thread safety).
            checkpointer: Optional LangGraph checkpoint saver.  Defaults to in-memory.
        """
        import copy

        from antcrew.core.supervisor import Supervisor
        from antcrew.core.supervisor import parallel as _parallel

        agents: dict[str, object] = {}
        flow: list[tuple] = []

        for step in self._steps:
            name = step["name"]
            kwargs = step["agent_kwargs"]

            if step["parallel"]:
                instances = [cls(copy.copy(llm), **kwargs) for cls in step["agent_classes"]]
                agents[name] = _parallel(*instances, name=name)
            else:
                agents[name] = step["agent_class"](copy.copy(llm), **kwargs)

            for dep in step["deps"]:
                edge: tuple
                if step.get("when"):
                    edge = (dep, name, step["when"])
                else:
                    edge = (dep, name)
                flow.append(edge)

        if not flow:
            raise ValueError(
                "WorkflowBuilder: no flow edges produced. "
                "Did you forget to add steps with after= dependencies?"
            )

        supervisor = Supervisor(flow=flow)
        compiled = supervisor.build(agents, checkpointer=checkpointer)
        return Workflow(compiled, llm)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_deps(after: "str | list[str] | None") -> list[str]:
    if after is None:
        return []
    if isinstance(after, str):
        return [after]
    return list(after)
