"""TraceLog serializers — shared by antcrew trace --dump.

Not a CLI command itself; the `antcrew export` top-level command is in
ops_cmds.py and exports pipeline artifacts (zip). This module only
exposes ``_to_json`` / ``_to_csv`` helpers for trace --dump.
"""
from __future__ import annotations

# ── Serializers ───────────────────────────────────────────────────────────────

def _to_json(runs: list[dict]) -> str:
    import json

    def _default(obj):
        return str(obj)

    return json.dumps(runs, indent=2, default=_default) + "\n"


def _to_csv(runs: list[dict], *, include_calls: bool = False) -> str:
    import csv
    import io

    buf = io.StringIO()
    run_fields = [
        "id", "thread_id", "team", "status", "cost_usd",
        "started_at", "ended_at", "request",
    ]

    if not runs:
        writer = csv.DictWriter(buf, fieldnames=run_fields)
        writer.writeheader()
        return buf.getvalue()

    if not include_calls:
        writer = csv.DictWriter(buf, fieldnames=run_fields, extrasaction="ignore")
        writer.writeheader()
        for run in runs:
            writer.writerow(run)
    else:
        # Flatten: one row per agent_call, run fields repeated
        call_fields = [
            "agent_name", "duration_ms", "input_tokens",
            "output_tokens", "call_cost_usd",
        ]
        writer = csv.DictWriter(
            buf,
            fieldnames=run_fields + call_fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        for run in runs:
            calls = run.get("agent_calls") or []
            if not calls:
                row = {f: run.get(f, "") for f in run_fields}
                row.update({f: "" for f in call_fields})
                writer.writerow(row)
            else:
                for call in calls:
                    row = {f: run.get(f, "") for f in run_fields}
                    row.update({
                        "agent_name": call.get("agent_name", ""),
                        "duration_ms": call.get("duration_ms", ""),
                        "input_tokens": call.get("input_tokens", ""),
                        "output_tokens": call.get("output_tokens", ""),
                        "call_cost_usd": call.get("cost_usd", ""),
                    })
                    writer.writerow(row)

    return buf.getvalue()
