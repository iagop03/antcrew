"""
Checkpointer backends for persistent LangGraph thread state.

Thread state survives process restarts — runs with the same `thread_id`
resume where they left off instead of starting fresh.

Usage — context manager (recommended for scripts):
    from antcrew.checkpointers import SqliteSaver

    with SqliteSaver.from_conn_string("~/.antcrew/threads.db") as cp:
        team = DevTeam(checkpointer=cp)
        result = team.run("Build login module", thread_id="sprint-1")
        # Later (or next process):
        result2 = team.run("Add OAuth", thread_id="sprint-1")

Usage — persistent connection (long-lived processes / servers):
    import sqlite3
    from antcrew.checkpointers import SqliteSaver

    conn = sqlite3.connect("threads.db", check_same_thread=False)
    cp = SqliteSaver(conn)
    team = DevTeam(checkpointer=cp)

Requires: pip install antcrew[sqlite]
"""
from __future__ import annotations

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except ImportError:
    SqliteSaver = None  # type: ignore[assignment,misc]

__all__ = ["SqliteSaver"]
