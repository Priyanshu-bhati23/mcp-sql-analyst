"""Create a small demo e-commerce database: python -m sql_analyst.seed demo.db"""

from __future__ import annotations

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    country TEXT NOT NULL,
    signed_up DATE NOT NULL
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    ordered_at DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid', 'refunded', 'cancelled'))
);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL
);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_items_order ON order_items(order_id);
"""

PRODUCTS = [
    ("Mechanical keyboard", "peripherals", 8900), ("Wireless mouse", "peripherals", 2900),
    ("USB-C hub", "peripherals", 4500), ("27-inch monitor", "displays", 24900),
    ("Portable monitor", "displays", 15900), ("Laptop stand", "accessories", 3900),
    ("Webcam", "accessories", 6900), ("Desk mat", "accessories", 1900),
    ("Noise-cancelling headphones", "audio", 19900), ("USB microphone", "audio", 9900),
]  # fmt: skip
COUNTRIES = ["IN", "IN", "IN", "US", "US", "DE", "GB", "SG"]
FIRST = ["Asha", "Ravi", "Meera", "Arjun", "Lena", "Tom", "Sara", "Wei", "Noor", "Dev"]
LAST = ["Rao", "Shah", "Iyer", "Khan", "Meyer", "Hill", "Lopez", "Chen", "Ali", "Nair"]


def build(path: str | Path, customers: int = 120, orders: int = 600, seed: int = 7) -> Path:
    """Deterministic for a given seed, so tests and docs can quote exact numbers."""
    path = Path(path)
    path.unlink(missing_ok=True)
    rng = random.Random(seed)
    start = date(2026, 1, 1)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", [(i + 1, *p) for i, p in enumerate(PRODUCTS)])
    for cid in range(1, customers + 1):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = f"{name.lower().replace(' ', '.')}{cid}@example.com"
        signed = start + timedelta(days=rng.randrange(0, 240))
        conn.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
            (cid, name, email, rng.choice(COUNTRIES), signed.isoformat()),
        )
    item_id = 1
    for oid in range(1, orders + 1):
        ordered = start + timedelta(days=rng.randrange(0, 300))
        status = rng.choices(["paid", "refunded", "cancelled"], weights=[88, 7, 5])[0]
        conn.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?)",
            (oid, rng.randrange(1, customers + 1), ordered.isoformat(), status),
        )
        for product in rng.sample(range(1, len(PRODUCTS) + 1), k=rng.choice([1, 1, 2, 3])):
            conn.execute(
                "INSERT INTO order_items VALUES (?, ?, ?, ?)", (item_id, oid, product, rng.choice([1, 1, 1, 2]))
            )
            item_id += 1
    conn.commit()
    conn.close()
    return path


if __name__ == "__main__":
    target = build(sys.argv[1] if len(sys.argv) > 1 else "demo.db")
    print(f"created {target}")
