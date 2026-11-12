"""The MCP server: four tools and a schema resource over one read-only database."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .audit import AuditLog
from .database import Database
from .guard import QueryRejected

INSTRUCTIONS = """Read-only SQL access to a SQLite database.
Workflow: call list_tables, then describe_table for the tables you need, then run_query.
Only single SELECT statements are accepted. Results are capped; aggregate in SQL instead of
fetching raw rows. If a query is rejected, the error says why - fix the query and retry."""


def create_server(database: Database, audit: AuditLog | None = None) -> MCPServer:
    audit = audit or AuditLog(None)
    server = MCPServer("sql-analyst", instructions=INSTRUCTIONS, log_level="WARNING")

    @server.tool()
    def list_tables() -> list[dict]:
        """List the tables available for querying, with their row counts."""
        tables = database.tables()
        audit.record("list_tables", "ok", tables=len(tables))
        return tables

    @server.tool()
    def describe_table(table: str) -> dict:
        """Show a table's columns (name, type, nullability, keys) and foreign keys.

        Columns with "queryable": false are restricted and cannot be selected.
        """
        try:
            description = database.describe(table)
        except QueryRejected as exc:
            audit.record("describe_table", "rejected", table=table, error=str(exc))
            raise ToolError(str(exc)) from exc  # ToolError text is shown to the model
        audit.record("describe_table", "ok", table=table)
        return description

    @server.tool()
    def run_query(sql: str) -> dict:
        """Run one read-only SELECT statement (SQLite dialect) and return the rows.

        Returns columns, rows, row_count, truncated (true if the row cap cut the result
        short), and sql_executed (the statement as run, with the server's LIMIT applied).
        Writes, DDL, PRAGMA, multiple statements and system tables are rejected.
        """
        try:
            result = database.query(sql)
        except QueryRejected as exc:
            audit.record("run_query", "rejected", sql=sql, error=str(exc))
            raise ToolError(str(exc)) from exc  # ToolError text is shown to the model
        audit.record(
            "run_query", "ok", sql=sql, sql_executed=result.sql_executed, rows=result.row_count,
            truncated=result.truncated, elapsed_ms=round(result.elapsed_ms, 2),
        )  # fmt: skip
        return result.as_dict()

    @server.tool()
    def sample_rows(table: str, n: int = 5) -> dict:
        """Return up to `n` (max 20) example rows from a table, to see what the values look like."""
        description = describe_table(table)
        columns = [c["name"] for c in description["columns"] if c["queryable"]]
        quoted = ", ".join(f'"{c}"' for c in columns)
        return run_query(f'SELECT {quoted} FROM "{description["table"]}" LIMIT {max(1, min(int(n), 20))}')

    @server.resource("schema://overview")
    def schema_overview() -> str:
        """The whole schema as text: one line per column, plus foreign keys."""
        lines = []
        for entry in database.tables():
            description = database.describe(entry["table"])
            lines.append(f"{description['table']} ({entry['rows']} rows)")
            for column in description["columns"]:
                flags = [column["type"]]
                flags += ["primary key"] if column["primary_key"] else []
                flags += [] if column["nullable"] else ["not null"]
                flags += [] if column["queryable"] else ["RESTRICTED"]
                lines.append(f"  {column['name']}: {', '.join(flags)}")
            lines += [f"  {fk['column']} -> {fk['references']}" for fk in description["foreign_keys"]]
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    return server
