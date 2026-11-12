import sqlite3

import pytest

from sql_analyst.database import Database
from sql_analyst.guard import QueryRejected


def test_schema_introspection(db):
    assert db.tables() == [
        {"table": "customers", "rows": 120},
        {"table": "order_items", "rows": 1092},
        {"table": "orders", "rows": 600},
        {"table": "products", "rows": 10},
    ]
    orders = db.describe("ORDERS")  # case-insensitive
    assert [c["name"] for c in orders["columns"]] == ["id", "customer_id", "ordered_at", "status"]
    assert orders["columns"][0] == {
        "name": "id",
        "type": "INTEGER",
        "nullable": False,
        "primary_key": True,
        "queryable": True,
    }
    assert orders["foreign_keys"] == [{"column": "customer_id", "references": "customers.id"}]
    with pytest.raises(QueryRejected, match="Unknown table 'nope'"):
        db.describe("nope")


def test_query_returns_columns_rows_and_the_sql_that_ran(db):
    result = db.query("select status, count(*) as n from orders group by status order by status")
    assert result.columns == ["status", "n"]
    assert result.rows == [["cancelled", 28], ["paid", 534], ["refunded", 38]]
    assert sum(r[1] for r in result.rows) == 600 and not result.truncated
    assert result.sql_executed.endswith("LIMIT 201")


def test_results_are_capped_and_flagged(db_path):
    db = Database(db_path, max_rows=25)
    capped = db.query("SELECT id FROM orders")
    assert capped.row_count == 25 and capped.truncated
    exact = db.query("SELECT id FROM products")
    assert exact.row_count == 10 and not exact.truncated
    asked = db.query("SELECT id FROM orders LIMIT 5")
    assert asked.row_count == 5 and not asked.truncated  # the caller asked for 5 and got 5


def test_runaway_query_is_stopped_by_the_timeout(db_path):
    db = Database(db_path, timeout_seconds=0.2)
    forever = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    with pytest.raises(QueryRejected, match="exceeded the 0.2s time limit"):
        db.query(forever)


def test_denied_column_cannot_be_read_by_any_phrasing(db_path):
    db = Database(db_path, denied_columns={("customers", "email")})
    for sql in [
        "SELECT email FROM customers",
        "SELECT * FROM customers",
        "SELECT c.* FROM customers c",
        "SELECT upper(EMAIL) FROM customers",
        "SELECT name FROM customers WHERE email LIKE 'a%'",  # filtering on it would leak it bit by bit
        "SELECT name FROM customers ORDER BY email",
        "WITH t AS (SELECT * FROM customers) SELECT * FROM t",
        "SELECT (SELECT email FROM customers LIMIT 1)",
    ]:
        with pytest.raises(QueryRejected, match="customers.email is not permitted"):
            db.query(sql)
    assert db.query("SELECT name, country FROM customers LIMIT 1").row_count == 1
    assert next(c for c in db.describe("customers")["columns"] if c["name"] == "email")["queryable"] is False


def test_denied_table_is_invisible_and_unqueryable(db_path):
    db = Database(db_path, denied_tables={"customers"})
    assert "customers" not in [t["table"] for t in db.tables()]
    with pytest.raises(QueryRejected, match="Unknown or disallowed table 'customers'"):
        db.query("SELECT * FROM customers")


def test_the_connection_itself_is_read_only_even_without_the_guard(db):
    """Layers 2 and 3 hold on their own: bypass validation and try to write directly."""
    conn = db._connect()
    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
        conn.execute("DELETE FROM orders")
    conn.close()

    conn = db._connect()
    conn.set_authorizer(db._authorizer([]))
    for statement in [
        "DELETE FROM orders",
        "CREATE TABLE x (a)",
        "PRAGMA journal_mode = DELETE",
        "ATTACH ':memory:' AS m",
    ]:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
            conn.execute(statement)
    conn.close()
    assert db.query("SELECT count(*) FROM orders").rows == [[600]]  # nothing was changed


def test_large_cells_and_blobs_are_shortened(tmp_path):
    path = tmp_path / "blobs.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE docs (body TEXT, raw BLOB)")
    conn.execute("INSERT INTO docs VALUES (?, ?)", ("x" * 2000, b"\x00" * 64))
    conn.commit()
    conn.close()
    row = Database(path, max_cell_chars=100).query("SELECT body, raw FROM docs").rows[0]
    assert row[0].startswith("x" * 100) and row[0].endswith("[1900 more chars]") and row[1] == "<64 bytes>"


def test_sqlite_errors_are_reported_to_the_caller(db):
    with pytest.raises(QueryRejected, match="no such column: nope"):
        db.query("SELECT nope FROM orders")


def test_missing_database_file():
    with pytest.raises(FileNotFoundError):
        Database("/nonexistent/demo.db")
