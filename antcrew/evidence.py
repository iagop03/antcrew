"""EvidencePackage — first-class governed execution record.

Every antcrew execution that completes (or fails) produces an EvidencePackage.
It captures what happened, who approved it, how much it cost, and whether the
audit chain is intact.

Usage:
    from antcrew.trace import TraceLog
    from antcrew.evidence import EvidencePackage

    trace = TraceLog("~/.antcrew/trace.db")
    pkg   = EvidencePackage.from_trace(trace, run_id)
    print(pkg.to_dict())           # JSON-serialisable
    print(pkg.chain_status)        # "intact" | "broken" | "empty" | "unverifiable"
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AgentRecord:
    agent_name: str
    model_id:   str
    provider:   str
    duration_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class HitlRecord:
    step:        str
    verdict:     str
    reviewer_id: str
    reason:      str
    decided_at:  str
    row_hash:    str = ""


@dataclass
class EvidencePackage:
    """Structured, verifiable record of a single governed execution."""

    run_id:          str
    team:            str
    request_preview: str
    status:          str

    agents:          list[AgentRecord] = field(default_factory=list)
    hitl_decisions:  list[HitlRecord]  = field(default_factory=list)

    cost_usd:         float = 0.0
    duration_seconds: float = 0.0

    chain_status:  str = "empty"   # intact | broken | empty | unverifiable
    chain_message: str = ""

    generated_at:   str = ""
    engine_version: str = ""

    # ------------------------------------------------------------------ #
    # Construction                                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_trace(cls, trace_log, run_id: str) -> "EvidencePackage":
        """Build an EvidencePackage from a TraceLog for a given run_id.

        Returns a package with ``status="not_found"`` if the run is missing.
        """
        try:
            from antcrew import __version__ as _ver
        except Exception:
            _ver = "unknown"

        run = trace_log.get_run(run_id)
        if run is None:
            return cls(
                run_id=run_id,
                team="",
                request_preview="",
                status="not_found",
                generated_at=_now_iso(),
                engine_version=_ver,
            )

        # Compute duration
        started = run.get("started_at") or ""
        ended   = run.get("ended_at") or ""
        duration_s = 0.0
        if started and ended:
            try:
                t0 = datetime.fromisoformat(started)
                t1 = datetime.fromisoformat(ended)
                duration_s = (t1 - t0).total_seconds()
            except Exception:
                pass

        # Agents
        calls = trace_log.get_calls(run_id)
        agents = [
            AgentRecord(
                agent_name=c.get("agent_name", ""),
                model_id=c.get("model_id", ""),
                provider=c.get("provider", ""),
                duration_ms=c.get("duration_ms", 0.0),
                input_tokens=c.get("input_tokens", 0),
                output_tokens=c.get("output_tokens", 0),
                cost_usd=c.get("cost_usd", 0.0),
            )
            for c in calls
        ]

        # HITL decisions
        decisions_raw = trace_log.get_hitl_decisions(run_id)
        decisions = [
            HitlRecord(
                step=d.get("step", ""),
                verdict=d.get("decision", ""),
                reviewer_id=d.get("reviewer_id", ""),
                reason=d.get("reason", ""),
                decided_at=d.get("decided_at", ""),
                row_hash=d.get("row_hash", ""),
            )
            for d in decisions_raw
        ]

        # Hash chain
        chain_result   = trace_log.verify_hitl_chain()
        valid          = chain_result.get("valid")
        chain_message  = chain_result.get("message", "")
        if valid is True:
            chain_status = "intact"
        elif valid is False:
            chain_status = "broken"
        elif not decisions:
            chain_status = "empty"
        else:
            chain_status = "unverifiable"

        return cls(
            run_id=run_id,
            team=run.get("team", ""),
            request_preview=(run.get("request") or "")[:200],
            status=run.get("status", ""),
            agents=agents,
            hitl_decisions=decisions,
            cost_usd=float(run.get("cost_usd") or 0.0),
            duration_seconds=duration_s,
            chain_status=chain_status,
            chain_message=chain_message,
            generated_at=_now_iso(),
            engine_version=_ver,
        )

    # ------------------------------------------------------------------ #
    # Serialisation                                                        #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
            "schema_version":   "1.0",
            "run_id":           self.run_id,
            "team":             self.team,
            "request_preview":  self.request_preview,
            "status":           self.status,
            "cost_usd":         self.cost_usd,
            "duration_seconds": self.duration_seconds,
            "agents": [
                {
                    "agent_name":    a.agent_name,
                    "model_id":      a.model_id,
                    "provider":      a.provider,
                    "duration_ms":   a.duration_ms,
                    "input_tokens":  a.input_tokens,
                    "output_tokens": a.output_tokens,
                    "cost_usd":      a.cost_usd,
                }
                for a in self.agents
            ],
            "hitl_decisions": [
                {
                    "step":        d.step,
                    "verdict":     d.verdict,
                    "reviewer_id": d.reviewer_id,
                    "reason":      d.reason,
                    "decided_at":  d.decided_at,
                    "row_hash":    d.row_hash,
                }
                for d in self.hitl_decisions
            ],
            "chain_status":     self.chain_status,
            "chain_message":    self.chain_message,
            "generated_at":     self.generated_at,
            "engine_version":   self.engine_version,
            "document_hash":    self._document_hash(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def _document_hash(self) -> str:
        payload = {
            "run_id":          self.run_id,
            "team":            self.team,
            "status":          self.status,
            "cost_usd":        self.cost_usd,
            "chain_status":    self.chain_status,
            "hitl_decisions":  [
                {"step": d.step, "verdict": d.verdict,
                 "decided_at": d.decided_at, "row_hash": d.row_hash}
                for d in self.hitl_decisions
            ],
        }
        return "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @property
    def hitl_count(self) -> int:
        return len(self.hitl_decisions)

    @property
    def approved_count(self) -> int:
        return sum(1 for d in self.hitl_decisions if "approve" in d.verdict.lower())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
