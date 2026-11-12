"""Static validation of model-written SQL, before it gets near the database.

This is the first of three independent layers (the others are a read-only
connection and a SQLite authorizer in `database.py`). Its job is to reject
anything that is not a single read query, with an error message specific
enough for the model to correct itself, and to bound the result size.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

# Statement types that are never acceptable anywhere in the tree.
_FORBIDDEN: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,  # sqlglot's bucket for statements it does not model (VACUUM, REINDEX, ...)
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)
# SQLite functions that reach outside the query: file access, extensions, raw schema edits.
_FORBIDDEN_FUNCTIONS = frozenset(
    {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "sqlite_compileoption_get", "zipfile"}
)


class QueryRejected(ValueError):
    """The SQL is not allowed. The message is written for the model to read."""


@dataclass(frozen=True)
class Validated:
    sql: str  # the statement that will actually run (LIMIT applied)
    tables: tuple[str, ...]
    limit: int  # maximum rows to return to the caller
    capped: bool  # True when the server imposed the limit (the query had none, or asked for more)


def validate(sql: str, allowed_tables: set[str] | None = None, max_rows: int = 200) -> Validated:
    if len(sql) > 20_000:
        raise QueryRejected("Query is too long (limit 20,000 characters).")
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except SqlglotError as exc:
        raise QueryRejected(f"SQL syntax error: {str(exc).splitlines()[0]}") from exc
    if not statements:
        raise QueryRejected("Query is empty.")
    if len(statements) > 1:
        raise QueryRejected("Only one statement per call is allowed; remove everything after the first ';'.")

    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise QueryRejected(f"Only SELECT queries are allowed (got {tree.key.upper()}).")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            raise QueryRejected(f"{node.key.upper()} is not allowed; this connection is read-only.")
        if isinstance(node, exp.Into):
            raise QueryRejected("SELECT ... INTO is not allowed; this connection is read-only.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _FORBIDDEN_FUNCTIONS:
                raise QueryRejected(f"Function {name}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = sorted({t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in cte_names})
    for table in tables:
        if table.startswith("sqlite_"):
            raise QueryRejected("System tables are not queryable; use the list_tables and describe_table tools.")
        if allowed_tables is not None and table not in allowed_tables:
            raise QueryRejected(
                f"Unknown or disallowed table {table!r}. Available: {', '.join(sorted(allowed_tables))}."
            )

    # Bound the result. A LIMIT the query asked for (within the cap) is honoured as written.
    # Otherwise the server imposes max_rows and fetches one extra row to detect truncation.
    limit_node = tree.args.get("limit")
    requested = None
    if limit_node is not None:
        literal = limit_node.expression
        if isinstance(literal, exp.Literal) and literal.is_int:
            requested = int(literal.this)
        tree.set("limit", None)
    if requested is not None and requested <= max_rows:
        return Validated(tree.limit(requested).sql(dialect="sqlite"), tuple(tables), requested, capped=False)
    return Validated(tree.limit(max_rows + 1).sql(dialect="sqlite"), tuple(tables), max_rows, capped=True)
