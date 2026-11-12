import pytest

from sql_analyst.guard import QueryRejected, validate

TABLES = {"customers", "orders", "order_items", "products"}


def check(sql, max_rows=200):
    return validate(sql, TABLES, max_rows)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM customers",
        "select c.name, count(*) from customers c join orders o on o.customer_id = c.id group by 1",
        "WITH paid AS (SELECT * FROM orders WHERE status = 'paid') SELECT count(*) FROM paid",
        "SELECT name FROM customers UNION SELECT name FROM products",
        "SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE country = 'IN')",
        "SELECT name FROM customers;",  # a trailing semicolon is fine
        "SELECT 'DROP TABLE orders' AS note",  # keywords inside a string literal are data
    ],
)
def test_read_queries_are_accepted(sql):
    assert check(sql).sql.upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("DELETE FROM orders", "Only SELECT"),
        ("UPDATE orders SET status = 'paid'", "Only SELECT"),
        ("INSERT INTO orders VALUES (1, 1, '2026-01-01', 'paid')", "Only SELECT"),
        ("DROP TABLE orders", "Only SELECT"),
        ("CREATE TABLE x (a int)", "Only SELECT"),
        ("ALTER TABLE orders ADD COLUMN x int", "Only SELECT"),
        ("PRAGMA table_info(orders)", "Only SELECT"),
        ("ATTACH DATABASE '/tmp/other.db' AS other", "Only SELECT"),
        ("VACUUM", "Only SELECT"),
        ("SELECT 1; DROP TABLE orders", "Only one statement"),
        ("SELECT 1; SELECT 2", "Only one statement"),
        ("SELECT * FROM sqlite_master", "System tables"),
        ("SELECT * FROM payroll", "Unknown or disallowed table 'payroll'"),
        ("SELECT load_extension('/tmp/evil.so')", "Function load_extension"),
        ("SELECT readfile('/etc/passwd')", "Function readfile"),
        ("WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x", "not allowed"),
        ("", "empty"),
        ("   ;  ", "empty"),
        ("SELECT * FROM (", "syntax error"),
        ("SELECT " + "1, " * 10_000 + "1", "too long"),
    ],
)
def test_everything_else_is_rejected_with_a_reason(sql, message):
    with pytest.raises(QueryRejected, match=message):
        check(sql)


def test_cte_names_are_not_mistaken_for_tables():
    assert check("WITH payroll AS (SELECT id FROM customers) SELECT * FROM payroll").tables == ("customers",)


def test_tables_are_collected_case_insensitively():
    assert check('SELECT * FROM "Customers" JOIN ORDERS ON 1 = 1').tables == ("customers", "orders")


def test_limit_is_added_when_missing_with_one_extra_row_to_detect_truncation():
    result = check("SELECT name FROM customers", max_rows=50)
    assert result.sql.endswith("LIMIT 51") and result.limit == 50 and result.capped


def test_oversized_limit_is_clamped_and_small_limit_is_kept():
    big = check("SELECT name FROM customers LIMIT 100000", max_rows=50)
    assert big.sql.endswith("LIMIT 51") and big.limit == 50 and big.capped
    small = check("SELECT name FROM customers LIMIT 3", max_rows=50)
    assert small.sql.endswith("LIMIT 3") and small.limit == 3 and not small.capped


def test_limit_applies_to_the_whole_union():
    result = check("SELECT name FROM customers UNION ALL SELECT name FROM products", max_rows=10)
    assert result.sql.endswith("LIMIT 11")


def test_without_an_allowlist_any_non_system_table_passes():
    assert validate("SELECT * FROM anything").tables == ("anything",)
