"""Read-only SQLite access with defence in depth.

Layer 2: the file is opened with `mode=ro`, so the OS-level handle cannot write.
Layer 3: an authorizer callback is consulted by SQLite for every operation the
         compiled statement would perform; anything other than reading an allowed
         column or calling a function is denied - regardless of how the SQL was
         phrased. This is what makes column-level denial hold even for `SELECT *`.
Plus a wall-clock timeout (progress handler) and caps on rows and cell size.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .guard import QueryRejected, validate

_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
_BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    sql_executed: str
    elapsed_ms: float

    def as_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "sql_executed": self.sql_executed,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


@dataclass
class Database:
    path: Path
    max_rows: int = 200
    timeout_seconds: float = 5.0
    max_cell_chars: int = 500
    denied_columns: set[tuple[str, str]] = field(default_factory=set)  # {("customers", "email"), ...}
    denied_tables: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.path.is_file():
            raise FileNotFoundError(f"database file not found: {self.path}")
        self.denied_columns = {(t.lower(), c.lower()) for t, c in self.denied_columns}
        self.denied_tables = {t.lower() for t in self.denied_tables}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _authorizer(self, denied: list[str]):
        def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
            if action not in _ALLOWED_ACTIONS:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ:
                table, column = (arg1 or "").lower(), (arg2 or "").lower()
                if table.startswith("sqlite_") or table in self.denied_tables:
                    denied.append(table)
                    return sqlite3.SQLITE_DENY
                if (table, column) in self.denied_columns:
                    denied.append(f"{table}.{column}")
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        return authorize

    # --- schema introspection (not model-controlled SQL, so no authorizer needed) ---

    def tables(self) -> list[dict]:
        with self._connect() as conn:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            out = []
            for name in names:
                if name.lower() in self.denied_tables:
                    continue
                count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                out.append({"table": name, "rows": count})
            return out

    def table_names(self) -> set[str]:
        return {t["table"].lower() for t in self.tables()}

    def describe(self, table: str) -> dict:
        if table.lower() not in self.table_names():
            raise QueryRejected(f"Unknown table {table!r}. Available: {', '.join(sorted(self.table_names()))}.")
        with self._connect() as conn:
            real = conn.execute("SELECT name FROM sqlite_master WHERE lower(name) = ?", (table.lower(),)).fetchone()[0]
            columns = [
                {
                    "name": row[1],
                    "type": row[2] or "ANY",
                    "nullable": not row[3] and not row[5],
                    "primary_key": bool(row[5]),
                    "queryable": (real.lower(), row[1].lower()) not in self.denied_columns,
                }
                for row in conn.execute(f'PRAGMA table_info("{real}")')
            ]
            foreign_keys = [
                {"column": row[3], "references": f"{row[2]}.{row[4]}"}
                for row in conn.execute(f'PRAGMA foreign_key_list("{real}")')
            ]
        return {"table": real, "columns": columns, "foreign_keys": foreign_keys}

    # --- model-written SQL ---

    def query(self, sql: str) -> QueryResult:
        validated = validate(sql, self.table_names(), self.max_rows)
        denied: list[str] = []
        conn = self._connect()
        started = time.perf_counter()
        deadline = started + self.timeout_seconds
        conn.set_authorizer(self._authorizer(denied))
        # Called every 1000 VM instructions; a non-zero return aborts the statement.
        conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 1000)
        try:
            cursor = conn.execute(validated.sql)
            columns = [d[0] for d in cursor.description or []]
            fetched = cursor.fetchall()  # bounded by the LIMIT the guard applied
        except sqlite3.DatabaseError as exc:
            if denied:
                raise QueryRejected(f"Access to {', '.join(sorted(set(denied)))} is not permitted.") from exc
            if "interrupted" in str(exc):
                raise QueryRejected(
                    f"Query exceeded the {self.timeout_seconds:g}s time limit. Add filters or aggregate less data."
                ) from exc
            if "not authorized" in str(exc) or "readonly" in str(exc):
                raise QueryRejected("That operation is not permitted on this read-only connection.") from exc
            raise QueryRejected(f"SQLite error: {exc}") from exc
        finally:
            conn.close()

        rows = [[self._cell(v) for v in row] for row in fetched[: validated.limit]]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=validated.capped and len(fetched) > validated.limit,
            sql_executed=validated.sql,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _cell(self, value):
        if isinstance(value, bytes):
            return f"<{len(value)} bytes>"
        if isinstance(value, str) and len(value) > self.max_cell_chars:
            return value[: self.max_cell_chars] + f"... [{len(value) - self.max_cell_chars} more chars]"
        return value
