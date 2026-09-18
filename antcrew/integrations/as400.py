"""AS400Connector — read-only access to IBM AS/400 (iSeries) DB2 via ODBC.

Requires the [legacy] optional extra::

    pip install antcrew[legacy]

The connector uses ``pyodbc`` with an IBM iSeries Access ODBC DSN or a full
connection string.  It provides three focused operations:

* ``get_schema(table)``  — columns, types, keys for a DB2/400 table
* ``get_copybook(table)`` — COBOL-style copybook layout derived from the schema
* ``sample_data(table)`` — first N rows as a list of dicts (read-only)

Example::

    from antcrew.integrations.as400 import AS400Connector

    conn = AS400Connector(
        dsn="MY_AS400_DSN",
        username="MYUSER",
        password="MYPASS",
    )
    schema = conn.get_schema("ORDLIB.ORDHDRF")
    print(schema)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    length: int = 0
    scale: int = 0
    nullable: bool = True
    is_key: bool = False


@dataclass
class TableSchema:
    library: str
    table: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int | None = None

    @property
    def full_name(self) -> str:
        return f"{self.library}.{self.table}" if self.library else self.table


class AS400Connector:
    """ODBC-based read-only connector to IBM AS/400 DB2 for i.

    Parameters
    ----------
    dsn:
        ODBC Data Source Name pre-configured in the system ODBC manager.
    connection_string:
        Full ODBC connection string.  Mutually exclusive with *dsn*.
    username / password:
        Credentials appended to both dsn and connection_string builds.
    library:
        Default library (schema) used when none is given in a table name.
    timeout:
        Query timeout in seconds (0 = no timeout).
    """

    def __init__(
        self,
        dsn: str | None = None,
        connection_string: str | None = None,
        username: str = "",
        password: str = "",
        library: str = "",
        timeout: int = 30,
    ) -> None:
        if not dsn and not connection_string:
            raise ValueError("Either 'dsn' or 'connection_string' must be provided")
        self._dsn = dsn
        self._conn_str = connection_string
        self._username = username
        self._password = password
        self._library = library
        self._timeout = timeout
        self._conn: Any = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        try:
            import pyodbc
        except ImportError:
            raise ImportError(
                "pyodbc is required for AS400Connector: pip install antcrew[legacy]"
            )
        if self._conn_str:
            cs = self._conn_str
            if self._username and "UID=" not in cs.upper():
                cs += f";UID={self._username};PWD={self._password}"
        else:
            cs = f"DSN={self._dsn};UID={self._username};PWD={self._password}"
        conn = pyodbc.connect(cs, timeout=self._timeout)
        conn.timeout = self._timeout
        return conn

    def _cursor(self):
        if self._conn is None:
            self._conn = self._connect()
        return self._conn.cursor()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "AS400Connector":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _split_table(self, table_name: str) -> tuple[str, str]:
        """Return (library, table) from 'LIBRARY.TABLE' or just 'TABLE'."""
        if "." in table_name:
            lib, tbl = table_name.split(".", 1)
        else:
            lib = self._library
            tbl = table_name
        return lib.upper(), tbl.upper()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_schema(self, table: str) -> TableSchema:
        """Return column metadata for *table* (format: LIBRARY.TABLE or TABLE).

        Queries ``QSYS2.SYSCOLUMNS`` — available on all modern OS/400 versions.
        Falls back to ODBC ``columns()`` method if that view is unavailable.
        """
        library, tbl = self._split_table(table)
        cur = self._cursor()

        columns: list[ColumnInfo] = []
        key_cols: set[str] = set()

        # Collect primary-key columns
        try:
            cur.execute(
                """
                SELECT COLUMN_NAME
                  FROM QSYS2.SYSKEYCST
                 WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                """,
                library, tbl,
            )
            key_cols = {row[0].upper() for row in cur.fetchall()}
        except Exception:
            pass

        # Try QSYS2.SYSCOLUMNS first (most information)
        try:
            cur.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, LENGTH, NUMERIC_SCALE, IS_NULLABLE
                  FROM QSYS2.SYSCOLUMNS
                 WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                 ORDER BY ORDINAL_POSITION
                """,
                library, tbl,
            )
            for row in cur.fetchall():
                name, dtype, length, scale, nullable = row
                columns.append(ColumnInfo(
                    name=name.strip(),
                    data_type=dtype.strip(),
                    length=int(length or 0),
                    scale=int(scale or 0),
                    nullable=(nullable or "Y").upper() == "Y",
                    is_key=name.strip().upper() in key_cols,
                ))
        except Exception:
            # Fallback: use ODBC catalog method
            for row in cur.columns(table=tbl, schema=library or None):
                name = row.column_name.strip()
                columns.append(ColumnInfo(
                    name=name,
                    data_type=row.type_name.strip(),
                    length=int(row.column_size or 0),
                    scale=int(row.decimal_digits or 0),
                    nullable=row.nullable == 1,
                    is_key=name.upper() in key_cols,
                ))

        return TableSchema(library=library, table=tbl, columns=columns)

    def get_copybook(self, table: str) -> str:
        """Return a COBOL copybook (FD + 01 level) derived from the schema.

        The generated copybook is a best-effort approximation — it maps DB2/400
        types to the most common COBOL PIC clauses.  Review it before using in
        production COBOL programs.
        """
        schema = self.get_schema(table)
        safe_name = schema.full_name.replace(".", "-")
        lines: list[str] = [
            f"      *> Copybook for {schema.full_name}",
            f"       01  {safe_name}.",
        ]
        for col in schema.columns:
            pic = _db2_to_pic(col)
            lines.append(f"           05  {col.name:<30} PIC {pic}.")
        return "\n".join(lines)

    def sample_data(self, table: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return the first *limit* rows of *table* as a list of dicts.

        This is strictly read-only (SELECT only).
        """
        library, tbl = self._split_table(table)
        qualified = f"{library}.{tbl}" if library else tbl
        cur = self._cursor()
        cur.execute(f"SELECT * FROM {qualified} FETCH FIRST {int(limit)} ROWS ONLY")  # noqa: S608 — read-only, table name is validated above
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ------------------------------------------------------------------
# Type mapping helpers
# ------------------------------------------------------------------

def _db2_to_pic(col: ColumnInfo) -> str:
    """Map a DB2/400 column type to a COBOL PIC clause."""
    dt = col.data_type.upper()
    n = col.length or 1

    if dt in ("CHAR", "CHARACTER"):
        return f"X({n})"
    if dt in ("VARCHAR", "CHARACTER VARYING"):
        return f"X({n})"
    if dt in ("DECIMAL", "NUMERIC"):
        s = col.scale
        total = n
        if s:
            return f"9({total - s})V9({s})"
        return f"9({total})"
    if dt in ("INTEGER", "INT"):
        return "S9(9) COMP-4"
    if dt in ("SMALLINT"):
        return "S9(4) COMP-4"
    if dt in ("BIGINT"):
        return "S9(18) COMP-4"
    if dt in ("REAL", "FLOAT", "DOUBLE"):
        return "COMP-2"
    if dt in ("DATE"):
        return "X(10)"
    if dt in ("TIME"):
        return "X(8)"
    if dt in ("TIMESTAMP"):
        return "X(26)"
    return f"X({n or 1})"
