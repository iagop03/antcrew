"""
TraceLog — SQLite-backed per-agent call recorder.

Activated per run when trace_log= is passed to a team constructor or when
``antcrew run --trace <file.db>`` is used. Enables ``antcrew trace`` to
inspect agent timings, token counts, and costs for any past run.

Usage — constructor:
    from antcrew.trace import TraceLog

    trace = TraceLog("~/.antcrew/trace.db")
    team = DevTeam(trace_log=trace)
    team.run("Build login module", thread_id="sprint-1")

Usage — inspect:
    for run in trace.list_runs():
        print(run["team"], run["cost_usd"])

    calls = trace.get_calls(run["id"])
    for c in calls:
        print(c["agent_name"], f'{c["duration_ms"]:.0f}ms', c["input_tokens"])

CLI:
    antcrew run "Build login" --trace ~/.antcrew/trace.db
    antcrew trace ~/.antcrew/trace.db
    antcrew trace ~/.antcrew/trace.db <run_id>
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ReplayError(RuntimeError):
    """Raised when a TraceLog call cannot be replayed."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    request     TEXT NOT NULL,
    team        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    cost_usd    REAL,
    status      TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS agent_calls (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL REFERENCES runs(id),
    agent_name       TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    duration_ms      REAL NOT NULL,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0.0,
    prompt_snippet   TEXT NOT NULL DEFAULT '',
    response_snippet TEXT NOT NULL DEFAULT '',
    prompt_full      TEXT NOT NULL DEFAULT '',
    response_full    TEXT NOT NULL DEFAULT '',
    user_full        TEXT NOT NULL DEFAULT ''
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema (idempotent)."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(agent_calls)").fetchall()}
    for col in ("prompt_full", "response_full", "user_full"):
        if col not in existing:
            conn.execute(f"ALTER TABLE agent_calls ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceLog:
    """SQLite-backed recorder for pipeline runs and per-agent LLM calls.

    Thread-safe for single-process use (``check_same_thread=False``).
    Each ``team.run()`` produces one row in ``runs`` and N rows in
    ``agent_calls`` (one per ``llm.system()`` invocation).
    """

    def __init__(self, db_path: str | Path, *, full_trace: bool = False) -> None:
        self._path = Path(db_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.full_trace = full_trace
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        _migrate(self._conn)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write API (called during runs)
    # ------------------------------------------------------------------

    def begin_run(self, *, thread_id: str, request: str, team: str) -> str:
        """Insert a new run row and return its UUID."""
        run_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO runs(id, thread_id, request, team, started_at) VALUES (?,?,?,?,?)",
            (run_id, thread_id, request[:1000], team, _now_iso()),
        )
        self._conn.commit()
        return run_id

    def end_run(
        self, run_id: str, *, cost_usd: float = 0.0, status: str = "done"
    ) -> None:
        """Stamp ended_at, final cost, and status on a run row."""
        self._conn.execute(
            "UPDATE runs SET ended_at=?, cost_usd=?, status=? WHERE id=?",
            (_now_iso(), cost_usd, status, run_id),
        )
        self._conn.commit()

    def record_call(
        self,
        *,
        run_id: str,
        agent_name: str,
        duration_ms: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        prompt_snippet: str = "",
        response_snippet: str = "",
        prompt_full: str = "",
        response_full: str = "",
        user_full: str = "",
    ) -> int:
        """Insert one agent_calls row (one per llm.system() invocation).

        Returns the integer primary key of the inserted row.

        When ``self.full_trace`` is True the complete prompt, user message, and
        response are stored; otherwise those columns remain empty (snippets are
        always stored). ``user_full`` is required for :meth:`replay`.
        """
        _psnip = prompt_snippet or (prompt_full[:300] if prompt_full else "")
        _rsnip = response_snippet or (response_full[:300] if response_full else "")
        cur = self._conn.execute(
            """INSERT INTO agent_calls
               (run_id, agent_name, started_at, duration_ms,
                input_tokens, output_tokens, cost_usd,
                prompt_snippet, response_snippet,
                prompt_full, response_full, user_full)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, agent_name, _now_iso(), round(duration_ms, 2),
                input_tokens, output_tokens, round(cost_usd, 6),
                _psnip[:300], _rsnip[:300],
                prompt_full if self.full_trace else "",
                response_full if self.full_trace else "",
                user_full if self.full_trace else "",
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read API (used by antcrew trace)
    # ------------------------------------------------------------------

    def list_runs(self, limit: int = 20) -> list[dict]:
        """Return the most recent runs (newest first)."""
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> Optional[dict]:
        """Return a single run by its UUID, or None if not found."""
        row = self._conn.execute(
            "SELECT * FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_run_by_thread(self, thread_id: str) -> Optional[dict]:
        """Return the most recent run for a given thread_id."""
        row = self._conn.execute(
            "SELECT * FROM runs WHERE thread_id=? ORDER BY started_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_calls(self, run_id: str) -> list[dict]:
        """Return all agent_calls for a run, ordered by insertion time."""
        rows = self._conn.execute(
            "SELECT * FROM agent_calls WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_call_detail(self, call_id: int) -> Optional[dict]:
        """Return a single agent_call by its integer primary key.

        Includes ``prompt_full`` and ``response_full`` when they were recorded
        (i.e., when the TraceLog was created with ``full_trace=True``).
        Returns ``None`` if not found.
        """
        row = self._conn.execute(
            "SELECT * FROM agent_calls WHERE id=?", (call_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_runs_filtered(
        self,
        *,
        team: Optional[str] = None,
        status: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return filtered runs, newest first.

        Args:
            team:   Filter to runs whose team column equals this value.
            status: Filter to ``done`` or ``error`` runs (``None`` = all).
            since:  ISO timestamp string; only runs started at or after this
                    moment are included.
            limit:  Maximum number of rows to return.
        """
        clauses: list[str] = []
        params: list = []
        if team:
            clauses.append("team = ?")
            params.append(team)
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if since:
            clauses.append("started_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM runs {where} ORDER BY started_at DESC LIMIT ?",  # nosec B608
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(
        self,
        *,
        team: Optional[str] = None,
        since: Optional[str] = None,
    ) -> dict:
        """Return aggregate statistics, optionally scoped to *team* / *since*.

        Returns a dict with keys:
            total_runs, done_runs, error_runs, total_cost_usd, avg_cost_usd,
            total_input_tokens, total_output_tokens, first_run, last_run,
            by_team (list of {team, runs, done, cost_usd}).
        """
        clauses: list[str] = []
        params: list = []
        if team:
            clauses.append("r.team = ?")
            params.append(team)
        if since:
            clauses.append("r.started_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        row = self._conn.execute(
            f"""
            SELECT
                COUNT(*) as total_runs,
                SUM(CASE WHEN status = 'done'  THEN 1 ELSE 0 END) as done_runs,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_runs,
                COALESCE(SUM(cost_usd), 0.0)  as total_cost_usd,
                COALESCE(AVG(cost_usd), 0.0)  as avg_cost_usd,
                MIN(started_at) as first_run,
                MAX(started_at) as last_run
            FROM runs r
            {where.replace('r.team', 'team').replace('r.started_at', 'started_at')}
            """,
            params,
        ).fetchone()

        tok_row = self._conn.execute(
            f"""
            SELECT
                COALESCE(SUM(ac.input_tokens),  0) as total_input,
                COALESCE(SUM(ac.output_tokens), 0) as total_output
            FROM agent_calls ac
            JOIN runs r ON ac.run_id = r.id
            {where}
            """,
            params,
        ).fetchone()

        by_team_rows = self._conn.execute(
            f"""
            SELECT
                team,
                COUNT(*) as runs,
                SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) as done,
                COALESCE(SUM(cost_usd), 0.0) as cost_usd
            FROM runs r
            {where.replace('r.team', 'team').replace('r.started_at', 'started_at')}
            GROUP BY team
            ORDER BY runs DESC
            """,
            params,
        ).fetchall()

        return {
            "total_runs":          int(row["total_runs"] or 0),
            "done_runs":           int(row["done_runs"] or 0),
            "error_runs":          int(row["error_runs"] or 0),
            "total_cost_usd":      float(row["total_cost_usd"] or 0.0),
            "avg_cost_usd":        float(row["avg_cost_usd"] or 0.0),
            "total_input_tokens":  int(tok_row["total_input"] or 0),
            "total_output_tokens": int(tok_row["total_output"] or 0),
            "first_run":           row["first_run"],
            "last_run":            row["last_run"],
            "by_team": [dict(r) for r in by_team_rows],
        }

    def prune(self, days: int) -> int:
        """Delete runs (and their agent_calls) older than *days* days.

        Returns the number of run rows deleted.
        """
        from datetime import timedelta

        if days < 0:
            raise ValueError(f"days must be >= 0, got {days}")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self._conn.execute(
            "DELETE FROM agent_calls WHERE run_id IN "
            "(SELECT id FROM runs WHERE started_at < ?)",
            (cutoff,),
        )
        cur = self._conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Replay API
    # ------------------------------------------------------------------

    def replay(self, call_id: int, llm) -> dict:
        """Re-run a single recorded agent call with *llm* and return a diff.

        The TraceLog must have been created with ``full_trace=True`` —
        otherwise ``prompt_full`` and ``user_full`` are empty and this raises
        ``ReplayError``.

        Returns a dict with:
            call_id        — the replayed row id
            agent_name     — agent that made the original call
            original       — original response text
            replayed       — new response text from *llm*
            matched        — True when original == replayed
            tokens_in      — tokens consumed by the replay call
            tokens_out     — tokens produced by the replay call
            cost_usd       — cost of the replay call
            duration_ms    — wall-clock time of the replay call (ms)
        """
        import time as _time

        row = self.get_call_detail(call_id)
        if row is None:
            raise ReplayError(f"call_id {call_id} not found in TraceLog")
        if not row.get("prompt_full"):
            raise ReplayError(
                "prompt_full is empty — TraceLog must be created with full_trace=True to enable replay"
            )

        system_prompt = row["prompt_full"]
        user_msg = row.get("user_full") or ""

        usage_before = llm.get_usage_summary()
        t0 = _time.monotonic()
        replayed_output = llm.system(system_prompt, user_msg)
        duration_ms = (_time.monotonic() - t0) * 1000

        usage_after = llm.get_usage_summary()
        tokens_in  = usage_after.get("total_input_tokens",  0) - usage_before.get("total_input_tokens",  0)
        tokens_out = usage_after.get("total_output_tokens", 0) - usage_before.get("total_output_tokens", 0)
        cost_usd   = round(usage_after.get("total_cost_usd", 0.0) - usage_before.get("total_cost_usd", 0.0), 6)

        return {
            "call_id":    call_id,
            "agent_name": row["agent_name"],
            "original":   row["response_full"],
            "replayed":   replayed_output,
            "matched":    row["response_full"] == replayed_output,
            "tokens_in":  tokens_in,
            "tokens_out": tokens_out,
            "cost_usd":   cost_usd,
            "duration_ms": round(duration_ms, 2),
        }

    def replay_all(self, run_id: str, llm) -> list[dict]:
        """Re-run every agent call in *run_id* sequentially and return results.

        Calls :meth:`replay` for each row in insertion order.  Stops and
        re-raises ``ReplayError`` on the first call that cannot be replayed
        (e.g. ``full_trace=False`` rows).

        Useful for regression testing: run the same pipeline request again and
        compare each agent output to the original to detect model drift.
        """
        calls = self.get_calls(run_id)
        if not calls:
            raise ReplayError(f"No agent_calls found for run_id {run_id!r}")
        return [self.replay(c["id"], llm) for c in calls]

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
