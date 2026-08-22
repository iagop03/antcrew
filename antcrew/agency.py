"""UC9 — White-Label / Agency billing primitives.

Agencies and consultancies that resell antcrew pipelines to end clients need:
  1. Per-client run tagging (client_label) for margin reporting.
  2. Markup calculation (cost → billed_usd) so they can invoice at a multiple.
  3. A BillingRecord they can store, log, or send to their billing system.

The platform's GET /runs/margin endpoint (already live) uses client_label
stored in run events. WhiteLabelWrapper emits those events automatically.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from antcrew.core.events import bus

log = logging.getLogger(__name__)


@dataclass
class BillingRecord:
    """Cost + billing breakdown for one white-label agency run.

    Attributes:
        client_label:  Client identifier (passed through to run events).
        cost_usd:      Raw LLM cost paid by the agency.
        billed_usd:    Amount to invoice the client (cost × markup).
        margin_usd:    Gross margin in USD (billed - cost).
        margin_pct:    Gross margin as a percentage; ``None`` when billed is 0.
        run_id:        Antcrew run_id from the underlying team state.
        state:         Full LangGraph state dict from the wrapped team run.
    """

    client_label: str
    cost_usd: float
    billed_usd: float
    margin_usd: float
    margin_pct: Optional[float]
    run_id: str
    state: dict


class WhiteLabelWrapper:
    """UC9: Wrap any antcrew team for agency / reseller billing.

    Applies a markup percentage on top of the LLM cost to compute the amount
    billed to the end client, tags every run event with ``client_label``, and
    returns a :class:`BillingRecord` from every ``run()`` call.

    Works with any team that exposes a ``run(request, **kwargs)`` → ``RunResult``
    interface: ``DevTeam``, ``ContentTeam``, ``LegalReviewTeam``, custom teams,
    and so on.

    Usage::

        from antcrew import DevTeam, WhiteLabelWrapper

        team = DevTeam()
        billing = WhiteLabelWrapper(team, client_label="acme-corp", markup_pct=200)
        record = billing.run("Build a REST API for a todo app")

        print(f"Cost:   ${record.cost_usd:.4f}")
        print(f"Billed: ${record.billed_usd:.4f}")
        print(f"Margin: ${record.margin_usd:.4f}  ({record.margin_pct:.1f}%)")

    The ``markup_pct`` semantics::

        markup_pct=0    →  billed == cost (0 % markup, pass-through billing)
        markup_pct=100  →  billed == cost × 2 (100 % markup, 50 % margin)
        markup_pct=200  →  billed == cost × 3 (200 % markup, 66.7 % margin)
        markup_pct=300  →  billed == cost × 4 (default, 75 % margin)
    """

    def __init__(
        self,
        team: Any,
        *,
        client_label: str,
        markup_pct: float = 300.0,
    ) -> None:
        """
        Args:
            team:         Any antcrew team instance with a ``run()`` method.
            client_label: Client identifier used in run events and margin reports.
            markup_pct:   Percentage markup applied on top of raw LLM cost.
                          Must be >= 0. Default 300 (= 4× cost, 75 % gross margin).
        """
        if markup_pct < 0:
            raise ValueError(f"markup_pct must be >= 0, got {markup_pct}")
        if not client_label or not client_label.strip():
            raise ValueError("client_label must be a non-empty string")
        self._team = team
        self.client_label = client_label.strip()
        self.markup_pct = markup_pct

    def run(self, request: str, **kwargs) -> BillingRecord:
        """Delegate to the wrapped team and compute a :class:`BillingRecord`.

        Args:
            request: Prompt / task description forwarded to the wrapped team.
            **kwargs: Additional keyword arguments forwarded to ``team.run()``.

        Returns:
            :class:`BillingRecord` with billing breakdown.
        """
        bus.emit(
            "agency.run_start",
            {"client_label": self.client_label, "markup_pct": self.markup_pct},
        )
        result = self._team.run(request, **kwargs)
        cost = getattr(result, "cost_usd", 0.0) or 0.0
        multiplier = 1.0 + self.markup_pct / 100.0
        billed = round(cost * multiplier, 6)
        margin = round(billed - cost, 6)
        margin_pct = round((margin / billed) * 100, 2) if billed > 0 else None
        run_id = getattr(result, "state", {}).get("_run_id", "")

        bus.emit(
            "agency.run_end",
            {
                "client_label": self.client_label,
                "cost_usd": cost,
                "billed_usd": billed,
                "margin_usd": margin,
                "margin_pct": margin_pct,
                "run_id": run_id,
            },
        )
        log.debug(
            "WhiteLabel run for %r: cost=%.4f billed=%.4f margin=%.1f%%",
            self.client_label, cost, billed, margin_pct or 0,
        )
        return BillingRecord(
            client_label=self.client_label,
            cost_usd=cost,
            billed_usd=billed,
            margin_usd=margin,
            margin_pct=margin_pct,
            run_id=run_id,
            state=getattr(result, "state", {}),
        )
