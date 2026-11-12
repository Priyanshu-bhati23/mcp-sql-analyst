"""Run the server: python -m sql_analyst --db demo.db"""

from __future__ import annotations

import argparse
import sys

from .audit import AuditLog
from .database import Database
from .server import create_server


def parse_column(value: str) -> tuple[str, str]:
    table, dot, column = value.partition(".")
    if not dot or not table or not column:
        raise argparse.ArgumentTypeError(f"expected table.column, got {value!r}")
    return table, column


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sql_analyst", description="Read-only SQL MCP server for SQLite")
    parser.add_argument("--db", required=True, help="path to the SQLite database file")
    parser.add_argument("--max-rows", type=int, default=200, help="row cap per query (default 200)")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds before a query is aborted (default 5)")
    parser.add_argument("--deny-table", action="append", default=[], metavar="TABLE", help="hide a table entirely")
    parser.add_argument(
        "--deny-column", action="append", default=[], type=parse_column, metavar="TABLE.COLUMN",
        help="make a column unselectable, e.g. customers.email (repeatable)",
    )  # fmt: skip
    parser.add_argument("--audit-log", help="append every tool call to this JSONL file")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    args = parser.parse_args(argv)

    try:
        database = Database(
            args.db,
            max_rows=args.max_rows,
            timeout_seconds=args.timeout,
            denied_columns=set(args.deny_column),
            denied_tables=set(args.deny_table),
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    create_server(database, AuditLog(args.audit_log)).run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
