"""
GroupChat — multi-agent debate that converges to consensus.

Runs N agents in a round-robin debate until they agree or max_rounds is reached.
A neutral moderator (or the first agent's LLM) summarises each round and decides
whether consensus has been reached.

Usage::

    from antcrew import GroupChat
    from antcrew.agents import ArchitectAgent, SecurityAgent, DevOpsAgent

    chat = GroupChat([
        ArchitectAgent(llm),
        SecurityAgent(llm),
        DevOpsAgent(llm),
    ])
    result = chat.run("Should we use PostgreSQL or MongoDB for this project?")

    print(result.consensus)   # "CONSENSUS: PostgreSQL for …"
    print(result.agreed)      # True
    for r in result.rounds:
        print(r["round"], r["responses"])

The moderator prompt is intentionally lightweight — it checks for the literal
string ``"CONSENSUS:"`` at the start of its summary to detect agreement.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GroupChatResult:
    """Result of a :class:`GroupChat` run.

    Attributes:
        rounds:    List of per-round dicts: ``{round, topic, responses, summary}``.
        consensus: The final moderator summary (starts with ``"CONSENSUS:"`` if agreed).
        agreed:    ``True`` when the moderator detected consensus before max_rounds.
    """
    rounds:    list[dict]
    consensus: str
    agreed:    bool


_MODERATOR_SYSTEM = (
    "You are a neutral technical moderator. Your job is to summarise a multi-agent "
    "discussion and determine if consensus has been reached.\n\n"
    "Rules:\n"
    "1. If all agents substantially agree, start your response with 'CONSENSUS: ' "
    "followed by a concise summary of what was agreed.\n"
    "2. If disagreement remains, summarise the key points of contention and "
    "propose a focused next question for the agents.\n"
    "3. Be concise — max 150 words."
)


class GroupChat:
    """Run agents in debate mode until consensus or *max_rounds* is reached.

    Args:
        agents:     List of :class:`~antcrew.core.agent.BaseAgent` instances.
                    Each agent's ``system()`` method is called with the current topic.
        max_rounds: Maximum number of debate rounds (default: 4).
        moderator_llm:
                    Optional LLM for the moderator.  Defaults to the first agent's LLM.
    """

    def __init__(
        self,
        agents: list,
        *,
        max_rounds: int = 4,
        moderator_llm=None,
    ) -> None:
        if not agents:
            raise ValueError("GroupChat requires at least one agent.")
        self.agents = agents
        self.max_rounds = max_rounds
        self._moderator_llm = moderator_llm or getattr(agents[0], "llm", None)
        if self._moderator_llm is None:
            raise ValueError("GroupChat: could not resolve a moderator LLM.")

    def run(self, topic: str) -> GroupChatResult:
        """Run the debate and return a :class:`GroupChatResult`.

        Args:
            topic: The question or decision to debate.

        Returns:
            A :class:`GroupChatResult` with per-round history and final consensus.
        """
        rounds: list[dict] = []
        current_topic = topic

        for round_num in range(1, self.max_rounds + 1):
            # ── Collect responses from all agents ────────────────────
            responses: dict[str, str] = {}
            for agent in self.agents:
                name = getattr(agent, "name", type(agent).__name__)
                system_prompt = getattr(agent, "_system_prompt", None) or (
                    f"You are {name}. "
                    "Give your expert opinion on the following topic. "
                    "Be concise and specific — max 80 words."
                )
                try:
                    responses[name] = agent.system(system_prompt, current_topic)
                except Exception as exc:
                    responses[name] = f"[error: {exc}]"

            # ── Moderator summarises ─────────────────────────────────
            agent_block = "\n\n".join(
                f"{name}: {text}" for name, text in responses.items()
            )
            moderator_user = (
                f"Round {round_num}/{self.max_rounds}\n"
                f"Topic: {current_topic}\n\n"
                f"Agent responses:\n{agent_block}"
            )
            summary = self._moderator_llm.system(_MODERATOR_SYSTEM, moderator_user)

            rounds.append({
                "round":     round_num,
                "topic":     current_topic,
                "responses": responses,
                "summary":   summary,
            })

            if summary.strip().upper().startswith("CONSENSUS:"):
                return GroupChatResult(rounds=rounds, consensus=summary.strip(), agreed=True)

            # Next round: use moderator's summary as the next topic
            current_topic = summary.strip()

        return GroupChatResult(rounds=rounds, consensus=current_topic, agreed=False)

    def run_async(self, topic: str) -> GroupChatResult:
        """Thread-safe async wrapper — runs :meth:`run` in a thread pool."""
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(self.run, topic).result()
