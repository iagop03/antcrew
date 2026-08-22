from __future__ import annotations

from antcrew_engine.models.base import Message
from antcrew_engine.models.simulated import SimulatedLLM


class SequencedLLM(SimulatedLLM):
    """Returns responses in sequence — useful for testing retry logic and multi-call flows.

    Unlike SimulatedLLM (which picks a fixture based on the prompt), SequencedLLM
    returns responses in the order given. Raises StopIteration if exhausted.

    Usage:
        llm = SequencedLLM(['{"title": "PRD 1"}', '{"title": "PRD 2"}'])
        assert llm.complete([...]) == '{"title": "PRD 1"}'
        assert llm.call_count == 1
    """

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._responses = iter(responses)
        self.call_count = 0
        self.last_max_tokens: int | None = None

    def complete(self, messages: list[Message], *, max_tokens: int = 16384, json_mode: bool = False) -> str:
        self.call_count += 1
        self.last_max_tokens = max_tokens
        result = next(self._responses)
        if self.on_token:
            self.on_token(result)
        prompt = "".join(m.content for m in messages)
        self._record_usage(len(prompt) // 4, len(result) // 4)
        return result
