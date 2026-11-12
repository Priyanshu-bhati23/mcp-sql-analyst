"""Drive the server over stdio the way an MCP host would - no LLM needed.

python -m sql_analyst.seed demo.db
python examples/client_demo.py demo.db
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import Client, StdioServerParameters

QUERIES = [
    "SELECT country, COUNT(*) AS customers FROM customers GROUP BY country ORDER BY customers DESC",
    "SELECT name, email FROM customers",  # email is denied below
    "UPDATE orders SET status = 'paid'",
]


async def main(db: str) -> None:
    args = ["-m", "sql_analyst", "--db", db, "--deny-column", "customers.email"]
    async with Client(StdioServerParameters(command=sys.executable, args=args)) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools), "\n")
        for sql in QUERIES:
            result = await client.call_tool("run_query", {"sql": sql})
            print(">", sql)
            if result.is_error:
                print("  REJECTED:", result.content[0].text, "\n")
            else:
                data = json.loads(result.content[0].text)
                print("  columns:", data["columns"])
                for row in data["rows"]:
                    print("  ", row)
                print()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "demo.db"))
