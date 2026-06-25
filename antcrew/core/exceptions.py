"""
AntCrew domain exceptions.
"""
from __future__ import annotations


class CostLimitExceeded(RuntimeError):
    """Raised when a pipeline run exceeds the configured max_cost_usd budget.

    The run is aborted before the next LLM call is made.  If a SqliteSaver
    checkpointer is in use, state from completed nodes is already persisted
    and the run can be inspected or resumed.

    Attributes:
        cost_usd:  Accumulated cost (USD) at the point of interruption.
        limit_usd: The max_cost_usd budget that was exceeded.
    """

    def __init__(self, cost_usd: float, limit_usd: float) -> None:
        self.cost_usd = cost_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"Cost limit exceeded: ${cost_usd:.4f} spent >= ${limit_usd:.4f} limit"
        )
