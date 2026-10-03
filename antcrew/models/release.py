"""SDK-side Release and ReleaseItem dataclasses.

These are lightweight Python dataclasses (no SQLModel) — used by ChangePackager,
CLI commands, and anyone who doesn't want a platform database dependency.

Platform-side SQLModel counterparts live in antcrew-platform/app/models/release.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


ReleaseState = Literal["draft", "pending_approval", "approved", "rejected", "deployed"]
ReleaseItemOrigin = Literal["antcrew", "vcs_only"]


@dataclass
class ReleaseItem:
    """One change-request line within a Release.

    Attributes:
        release_id:  Parent release identifier.
        change_ref:  CR / ticket reference (e.g. "CR-1234").
        run_ids:     AntCrew run UUIDs that implemented this CR.
        origin:      ``antcrew`` if developed via AntCrew, ``vcs_only`` if
                     committed directly to VCS without an AntCrew run.
    """

    release_id: str
    change_ref: str
    run_ids: list[str] = field(default_factory=list)
    origin: ReleaseItemOrigin = "antcrew"
    summary: str = ""
    impact_risk: str = ""       # low | medium | high (from ImpactAnalysis)


@dataclass
class Release:
    """A named group of change-requests scheduled for production deployment.

    States:
        draft            → being assembled, not yet submitted for approval.
        pending_approval → submitted; waiting for required approvers.
        approved         → all required approvals collected.
        rejected         → at least one required approver rejected.
        deployed         → changes have been pushed to production.
    """

    id: str
    name: str
    state: ReleaseState = "draft"
    target_date: Optional[str] = None    # ISO-8601 date string
    items: list[ReleaseItem] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    notes: str = ""

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def add_item(self, change_ref: str, *, run_ids: list[str] | None = None,
                 origin: ReleaseItemOrigin = "antcrew") -> ReleaseItem:
        item = ReleaseItem(
            release_id=self.id,
            change_ref=change_ref,
            run_ids=run_ids or [],
            origin=origin,
        )
        self.items.append(item)
        return item

    @property
    def change_refs(self) -> list[str]:
        return [i.change_ref for i in self.items]

    @property
    def all_run_ids(self) -> list[str]:
        ids: list[str] = []
        for item in self.items:
            ids.extend(item.run_ids)
        return ids

    def submit_for_approval(self) -> None:
        if self.state != "draft":
            raise ValueError(f"Cannot submit release in state {self.state!r}.")
        self.state = "pending_approval"

    def approve(self) -> None:
        if self.state not in ("pending_approval", "approved"):
            raise ValueError(f"Cannot approve release in state {self.state!r}.")
        self.state = "approved"

    def reject(self) -> None:
        self.state = "rejected"

    def deploy(self) -> None:
        if self.state != "approved":
            raise ValueError(f"Cannot deploy release in state {self.state!r}; must be approved first.")
        self.state = "deployed"
