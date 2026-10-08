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
    chain_root:    str = ""        # final row_hash of the execution event chain
    event_count:   int = 0         # total execution events in the chain

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

        # Execution-wide hash chain (v6+); fall back to HITL-only chain for older DBs
        exec_chain = getattr(trace_log, "verify_execution_chain", None)
        chain_root = ""
        event_count = 0
        if exec_chain is not None:
            exec_result = exec_chain(run_id)
            valid = exec_result.get("valid")
            chain_message = exec_result.get("message", "")
            chain_root = exec_result.get("chain_root", "")
            event_count = exec_result.get("total", 0)
        else:
            exec_result = trace_log.verify_hitl_chain()
            valid = exec_result.get("valid")
            chain_message = exec_result.get("message", "")

        if valid is True:
            chain_status = "intact"
        elif valid is False:
            chain_status = "broken"
        elif not decisions and event_count == 0:
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
            chain_root=chain_root,
            event_count=event_count,
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
            "chain_root":       self.chain_root,
            "event_count":      self.event_count,
            "generated_at":     self.generated_at,
            "engine_version":   self.engine_version,
            "document_hash":    self._document_hash(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_html(self) -> str:
        """Render a self-contained HTML evidence report suitable for archiving or printing to PDF."""
        d = self.to_dict()

        chain_color = {"intact": "#34D399", "broken": "#f87171", "empty": "#7A9AB5", "unverifiable": "#FBBF24"}.get(self.chain_status, "#7A9AB5")
        chain_icon  = {"intact": "✓", "broken": "✗", "empty": "○", "unverifiable": "?"}.get(self.chain_status, "?")
        status_color = "#34D399" if self.status == "done" else ("#f87171" if self.status == "error" else "#FBBF24")

        def _agents_rows() -> str:
            if not self.agents:
                return "<tr><td colspan='5' style='color:#7A9AB5;text-align:center'>No agent calls recorded</td></tr>"
            rows = []
            for a in self.agents:
                dur = f"{a.duration_ms/1000:.1f}s" if a.duration_ms >= 1000 else f"{a.duration_ms:.0f}ms"
                rows.append(
                    f"<tr><td>{_esc(a.agent_name)}</td><td style='color:#7A9AB5'>{_esc(a.model_id or '—')}</td>"
                    f"<td style='text-align:right'>{a.input_tokens}↑ {a.output_tokens}↓</td>"
                    f"<td style='text-align:right'>${a.cost_usd:.4f}</td>"
                    f"<td style='text-align:right'>{dur}</td></tr>"
                )
            return "\n".join(rows)

        def _decisions_rows() -> str:
            if not self.hitl_decisions:
                return "<tr><td colspan='5' style='color:#7A9AB5;text-align:center'>No HITL decisions recorded</td></tr>"
            rows = []
            for dec in self.hitl_decisions:
                v = dec.verdict
                vc = "#34D399" if "approve" in v else ("#f87171" if "reject" in v or "timeout" in v else "#FBBF24")
                when = (dec.decided_at or "")[:19].replace("T", " ")
                rows.append(
                    f"<tr><td style='font-family:monospace'>{_esc(dec.step)}</td>"
                    f"<td style='color:{vc};font-weight:600'>{_esc(v)}</td>"
                    f"<td style='color:#7A9AB5'>{_esc(dec.reviewer_id or '—')}</td>"
                    f"<td>{_esc(dec.reason[:60] if dec.reason else '—')}</td>"
                    f"<td style='color:#7A9AB5;font-family:monospace;font-size:11px'>{when}</td></tr>"
                )
            return "\n".join(rows)

        doc_hash = self._document_hash()
        cost_str = f"${self.cost_usd:.4f}" if self.cost_usd else "—"
        dur_str  = f"{self.duration_seconds:.0f}s" if self.duration_seconds else "—"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evidence · {_esc(self.run_id[:16])}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#080F1C;color:#E8EDF5;font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:14px;padding:32px 24px;max-width:900px;margin:0 auto}}
  h1{{font-family:Georgia,serif;font-size:22px;font-weight:700;margin-bottom:4px}}
  h2{{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:#4E6A85;margin:28px 0 12px}}
  .badge{{display:inline-block;padding:2px 10px;border-radius:2px;font-size:11px;font-weight:600;font-family:monospace}}
  .kv{{display:grid;grid-template-columns:140px 1fr;gap:8px 16px;margin-bottom:20px}}
  .kv .k{{color:#4E6A85;font-size:12px}}
  .kv .v{{font-family:monospace;font-size:12px;word-break:break-all}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{padding:8px 12px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:#4E6A85;border-bottom:1px solid #1E2D42}}
  td{{padding:9px 12px;border-bottom:1px solid #0F1929}}
  .footer{{margin-top:32px;padding-top:16px;border-top:1px solid #1E2D42;font-size:11px;color:#354E65;font-family:monospace;word-break:break-all}}
  @media print{{body{{background:#fff;color:#111}}th{{color:#555}}td{{border-color:#ddd}}h2{{color:#555}}.footer{{color:#888}}}}
</style>
</head>
<body>
<h1>Evidence Package</h1>
<p style="color:#7A9AB5;font-size:13px;margin:4px 0 24px">{_esc(self.request_preview or '(no request recorded)')}</p>

<div class="kv">
  <div class="k">Run ID</div>      <div class="v">{_esc(self.run_id)}</div>
  <div class="k">Team</div>        <div class="v">{_esc(self.team or '—')}</div>
  <div class="k">Status</div>      <div class="v"><span style="color:{status_color}">{_esc(self.status)}</span></div>
  <div class="k">Cost</div>        <div class="v">{cost_str}</div>
  <div class="k">Duration</div>    <div class="v">{dur_str}</div>
  <div class="k">HITL decisions</div> <div class="v">{self.hitl_count} ({self.approved_count} approved)</div>
  <div class="k">Chain integrity</div> <div class="v"><span style="color:{chain_color}">{chain_icon} {_esc(self.chain_status)}</span>{f' — {self.event_count} event(s)' if self.event_count else ''}</div>
  <div class="k">Engine</div>      <div class="v">{_esc(self.engine_version)}</div>
  <div class="k">Generated</div>   <div class="v">{_esc(self.generated_at[:19].replace('T',' '))}</div>
</div>

<h2>Agents</h2>
<table>
<thead><tr><th>Agent</th><th>Model</th><th>Tokens</th><th>Cost</th><th>Duration</th></tr></thead>
<tbody>{_agents_rows()}</tbody>
</table>

<h2>Human Decisions</h2>
<table>
<thead><tr><th>Step</th><th>Verdict</th><th>Reviewer</th><th>Reason</th><th>When</th></tr></thead>
<tbody>{_decisions_rows()}</tbody>
</table>

<div class="footer">
  Document hash: {doc_hash}<br>
  Chain status: {_esc(self.chain_status)}{(' — ' + _esc(self.chain_message)) if self.chain_message else ''}<br>
  {f'Chain root: {_esc(self.chain_root)}<br>' if self.chain_root else ''}Events recorded: {self.event_count}
</div>
</body>
</html>"""

    def _document_hash(self) -> str:
        payload = {
            "run_id":          self.run_id,
            "team":            self.team,
            "status":          self.status,
            "cost_usd":        self.cost_usd,
            "chain_status":    self.chain_status,
            "chain_root":      self.chain_root,
            "event_count":     self.event_count,
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


def _esc(s: str) -> str:
    """HTML-escape a string for safe embedding in evidence reports."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
