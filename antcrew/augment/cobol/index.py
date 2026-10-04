"""COBOL inverse index — SQLite-backed, incrementally updated from VCS changes.

The index answers three queries:
  1. ``who_calls(program)``    — programs that CALL this program (inverse call graph)
  2. ``who_includes(copybook)``— programs that COPY this copybook (inverse copy index)
  3. ``db2_tables(program)``   — DB2 tables read/written via EXEC SQL in this program

The index is built by scanning COBOL source files.  It is incremental:
``update_from_files(paths)`` rescans only the given files, keeping the rest intact.

Usage::

    from antcrew.augment.cobol.index import COBOLIndex

    idx = COBOLIndex("~/.antcrew/cobol.db")
    idx.build_from_directory("/srv/cobol/src")

    callers = idx.who_calls("ACCTUPD")           # → ["MAINPGM", "BATCHCTL"]
    includers = idx.who_includes("ACCTCPY")      # → ["ACCTUPD", "RPTRPT"]
    tables = idx.db2_tables("ACCTUPD")           # → [{"table": "ACCOUNT", "op": "UPDATE"}, ...]
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    name        TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    indexed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    caller  TEXT NOT NULL,
    callee  TEXT NOT NULL,
    PRIMARY KEY (caller, callee)
);

CREATE TABLE IF NOT EXISTS copies (
    program   TEXT NOT NULL,
    copybook  TEXT NOT NULL,
    PRIMARY KEY (program, copybook)
);

CREATE TABLE IF NOT EXISTS db2_access (
    program     TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    operation   TEXT NOT NULL,           -- SELECT | INSERT | UPDATE | DELETE | OPEN (cursor)
    PRIMARY KEY (program, table_name, operation)
);
"""

_RE_CALL = re.compile(r"\bCALL\s+['\"]?(\w+)['\"]?", re.IGNORECASE)
_RE_COPY = re.compile(r"^\s{0,7}COPY\s+(\w+)", re.IGNORECASE | re.MULTILINE)
_RE_EXEC_SQL = re.compile(
    r"EXEC\s+SQL\s+(SELECT|INSERT|UPDATE|DELETE|OPEN|FETCH|DECLARE\s+\w+\s+CURSOR\s+FOR\s+SELECT)"
    r".*?END-EXEC",
    re.IGNORECASE | re.DOTALL,
)
_RE_TABLE_FROM = re.compile(r"\bFROM\s+([\w.]+)", re.IGNORECASE)
_RE_TABLE_INTO = re.compile(r"\bINTO\s+([\w.]+)(?!\s+:)", re.IGNORECASE)   # exclude INTO :host-var
_RE_TABLE_UPDATE = re.compile(r"\bUPDATE\s+([\w.]+)", re.IGNORECASE)
_RE_TABLE_INSERT = re.compile(r"\bINSERT\s+INTO\s+([\w.]+)", re.IGNORECASE)
_RE_TABLE_DELETE = re.compile(r"\bDELETE\s+FROM\s+([\w.]+)", re.IGNORECASE)


class COBOLIndex:
    """SQLite-backed COBOL inverse index."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Build / update
    # ------------------------------------------------------------------

    def build_from_directory(
        self,
        src_dir: str | Path,
        *,
        extensions: tuple[str, ...] = (".cbl", ".cob", ".cobol", ".CBL", ".COB"),
    ) -> int:
        """Scan *src_dir* recursively and index all COBOL files. Returns file count."""
        paths = [p for p in Path(src_dir).rglob("*") if p.suffix in extensions]
        self.update_from_files(paths)
        return len(paths)

    def update_from_files(self, paths: list[Path]) -> None:
        """Re-index the given files; other entries in the DB are preserved."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        for path in paths:
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                name = path.stem.upper()
                self._index_file(name, str(path), source, now)
            except Exception:
                continue
        self._conn.commit()

    def _index_file(self, name: str, file_path: str, source: str, now: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO programs(name, file_path, indexed_at) VALUES (?,?,?)",
            (name, file_path, now),
        )
        self._conn.execute("DELETE FROM calls  WHERE caller  = ?", (name,))
        self._conn.execute("DELETE FROM copies WHERE program = ?", (name,))
        self._conn.execute("DELETE FROM db2_access WHERE program = ?", (name,))

        for m in _RE_CALL.finditer(source):
            callee = m.group(1).upper()
            if callee != name:
                self._conn.execute(
                    "INSERT OR IGNORE INTO calls(caller, callee) VALUES (?,?)",
                    (name, callee),
                )

        for m in _RE_COPY.finditer(source):
            copybook = m.group(1).upper()
            self._conn.execute(
                "INSERT OR IGNORE INTO copies(program, copybook) VALUES (?,?)",
                (name, copybook),
            )

        for m in _RE_EXEC_SQL.finditer(source):
            block = m.group(0).upper()
            verb = m.group(1).upper().split()[0]  # SELECT / INSERT / UPDATE / DELETE / OPEN / FETCH / DECLARE
            op = verb if verb in ("SELECT", "INSERT", "UPDATE", "DELETE") else "SELECT"
            tables: list[str] = []
            if verb in ("SELECT", "DECLARE"):
                tables += _RE_TABLE_FROM.findall(block)
            if verb == "INSERT":
                tables += _RE_TABLE_INSERT.findall(block)
            if verb == "UPDATE":
                tables += _RE_TABLE_UPDATE.findall(block)
            if verb == "DELETE":
                tables += _RE_TABLE_DELETE.findall(block)
            for tbl in tables:
                tbl = tbl.strip().upper()
                if not tbl or tbl.startswith(":"):
                    continue
                self._conn.execute(
                    "INSERT OR IGNORE INTO db2_access(program, table_name, operation) VALUES (?,?,?)",
                    (name, tbl, op),
                )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def who_calls(self, program: str) -> list[str]:
        """Return programs that CALL *program* (inverse call index)."""
        rows = self._conn.execute(
            "SELECT caller FROM calls WHERE callee = ? ORDER BY caller",
            (program.upper(),),
        ).fetchall()
        return [r["caller"] for r in rows]

    def who_includes(self, copybook: str) -> list[str]:
        """Return programs that COPY *copybook* (inverse copy index)."""
        rows = self._conn.execute(
            "SELECT program FROM copies WHERE copybook = ? ORDER BY program",
            (copybook.upper(),),
        ).fetchall()
        return [r["program"] for r in rows]

    def db2_tables(self, program: str) -> list[dict]:
        """Return DB2 tables accessed by *program* with their operations."""
        rows = self._conn.execute(
            "SELECT table_name, operation FROM db2_access WHERE program = ? ORDER BY table_name",
            (program.upper(),),
        ).fetchall()
        return [{"table": r["table_name"], "op": r["operation"]} for r in rows]

    def all_programs(self) -> list[str]:
        """Return all indexed program names."""
        rows = self._conn.execute("SELECT name FROM programs ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def stats(self) -> dict:
        """Return index statistics."""
        programs = self._conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0]
        calls = self._conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        copies = self._conn.execute("SELECT COUNT(*) FROM copies").fetchone()[0]
        db2 = self._conn.execute("SELECT COUNT(DISTINCT table_name) FROM db2_access").fetchone()[0]
        return {
            "programs": programs,
            "call_edges": calls,
            "copy_edges": copies,
            "db2_tables": db2,
        }

    def close(self) -> None:
        self._conn.close()
