"""End-to-end tests through the MCP protocol, using the SDK's in-process client."""

import asyncio
import json

from mcp import Client

from sql_analyst.__main__ import main, parse_column
from sql_analyst.audit import AuditLog
from sql_analyst.database import Database
from sql_analyst.server import create_server


def session(database, audit=None):
    """Run `steps(client)` against a server and return its result."""

    def run(steps):
        async def go():
            async with Client(create_server(database, audit)) as client:
                return await steps(client)

        return asyncio.run(go())

    return run


def payload(result):
    return json.loads(result.content[0].text)


def test_tools_are_advertised_with_descriptions_and_schemas(db):
    async def steps(client):
        return (await client.list_tools()).tools

    tools = {t.name: t for t in session(db)(steps)}
    assert set(tools) == {"list_tables", "describe_table", "run_query", "sample_rows"}
    assert "read-only SELECT" in tools["run_query"].description
    assert tools["run_query"].input_schema["required"] == ["sql"]


def test_a_typical_analysis_flow(db):
    async def steps(client):
        tables = await client.call_tool("list_tables", {})
        described = await client.call_tool("describe_table", {"table": "order_items"})
        answer = await client.call_tool(
            "run_query",
            {
                "sql": "SELECT p.category, SUM(oi.quantity * p.price_cents) / 100.0 AS revenue "
                "FROM order_items oi JOIN products p ON p.id = oi.product_id "
                "JOIN orders o ON o.id = oi.order_id WHERE o.status = 'paid' GROUP BY 1 ORDER BY 2 DESC"
            },
        )
        return tables, described, answer

    tables, described, answer = session(db)(steps)
    assert not tables.is_error and "order_items" in tables.content[0].text + str(tables.structured_content)
    assert {fk["references"] for fk in payload(described)["foreign_keys"]} == {"orders.id", "products.id"}
    result = payload(answer)
    assert result["columns"] == ["category", "revenue"]
    assert result["rows"][0] == ["displays", 54375.0] and len(result["rows"]) == 4


def test_rejections_reach_the_model_as_readable_tool_errors(db):
    async def steps(client):
        return [
            await client.call_tool("run_query", {"sql": sql})
            for sql in ("DROP TABLE orders", "SELECT 1; SELECT 2", "SELECT * FROM payroll")
        ]

    drop, multi, unknown = session(db)(steps)
    assert all(r.is_error for r in (drop, multi, unknown))
    assert "Only SELECT queries are allowed (got DROP)" in drop.content[0].text
    assert "Only one statement" in multi.content[0].text
    assert "Available: customers, order_items, orders, products" in unknown.content[0].text


def test_sample_rows_omits_restricted_columns_and_caps_n(db_path):
    database = Database(db_path, denied_columns={("customers", "email")})

    async def steps(client):
        return await client.call_tool("sample_rows", {"table": "customers", "n": 500})

    result = payload(session(database)(steps))
    assert result["columns"] == ["id", "name", "country", "signed_up"] and result["row_count"] == 20


def test_schema_resource(db_path):
    database = Database(db_path, denied_columns={("customers", "email")})

    async def steps(client):
        listed = await client.list_resources()
        return [str(r.uri) for r in listed.resources], (await client.read_resource("schema://overview")).contents[
            0
        ].text

    uris, text = session(database)(steps)
    assert uris == ["schema://overview"]
    assert "orders (600 rows)" in text and "customer_id -> customers.id" in text
    assert "email: TEXT, not null, RESTRICTED" in text


def test_every_call_is_audited_including_rejected_ones(db, tmp_path):
    log = tmp_path / "audit.jsonl"

    async def steps(client):
        await client.call_tool("list_tables", {})
        await client.call_tool("run_query", {"sql": "SELECT count(*) FROM orders"})
        await client.call_tool("run_query", {"sql": "DELETE FROM orders"})

    session(db, AuditLog(log))(steps)
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(e["tool"], e["status"]) for e in entries] == [
        ("list_tables", "ok"),
        ("run_query", "ok"),
        ("run_query", "rejected"),
    ]
    assert entries[1]["rows"] == 1 and entries[1]["sql_executed"].endswith("LIMIT 201")
    assert entries[2]["sql"] == "DELETE FROM orders" and "Only SELECT" in entries[2]["error"]


def test_cli_argument_handling(capsys):
    assert parse_column("customers.email") == ("customers", "email")
    assert main(["--db", "/nonexistent.db"]) == 2
    assert "database file not found" in capsys.readouterr().err
