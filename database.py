"""SQLite database layer for the Best Ever Competitor Pricing Tracker."""

import sqlite3
import os
from datetime import datetime, timedelta

from config import DB_PATH

# Category keywords for inference from product names
CATEGORY_KEYWORDS = {
    "shampoo": ["shampoo"],
    "conditioner": ["conditioner", "conditioning"],
    "mask": ["mask", "masque"],
    "treatment": ["treatment", "oil", "serum", "repair", "bond", "elixir"],
    "styling": ["spray", "gel", "mousse", "cream", "paste", "wax", "pomade", "dry shampoo"],
}


def infer_category(product_name: str) -> str:
    """Infer product category from the product name."""
    name_lower = product_name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in name_lower:
                return category
    return "other"


def get_connection():
    """Get a database connection, creating the data directory if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create database tables and indexes if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT NOT NULL,
            product_name TEXT NOT NULL,
            size TEXT,
            url TEXT UNIQUE NOT NULL,
            category TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            price REAL,
            regular_price REAL,
            sale_price REAL,
            date_scraped TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(date_scraped)")

    conn.commit()
    conn.close()


def upsert_product(brand: str, product_name: str, url: str, size: str = None, category: str = None) -> int:
    """Insert a new product or update last_seen if it already exists.

    Returns the product id.
    """
    if category is None:
        category = infer_category(product_name)

    now = datetime.now().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    # Check if product already exists by URL
    cur.execute("SELECT id FROM products WHERE url = ?", (url,))
    row = cur.fetchone()

    if row:
        product_id = row["id"]
        cur.execute(
            "UPDATE products SET last_seen = ?, size = COALESCE(?, size), category = COALESCE(?, category) WHERE id = ?",
            (now, size, category, product_id),
        )
    else:
        cur.execute(
            "INSERT INTO products (brand, product_name, size, url, category, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (brand, product_name, size, url, category, now, now),
        )
        product_id = cur.lastrowid

    conn.commit()
    conn.close()
    return product_id


def insert_price(product_id: int, price: float = None, regular_price: float = None, sale_price: float = None):
    """Insert a price record for a product."""
    now = datetime.now().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO price_history (product_id, price, regular_price, sale_price, date_scraped) VALUES (?, ?, ?, ?, ?)",
        (product_id, price, regular_price, sale_price, now),
    )

    conn.commit()
    conn.close()


def get_latest_prices(brand: str = None, category: str = None):
    """Get the most recent price for each product.

    Returns a list of dicts with product info and latest price data.
    """
    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT p.id, p.brand, p.product_name, p.size, p.url, p.category,
               p.first_seen, p.last_seen,
               ph.price, ph.regular_price, ph.sale_price, ph.date_scraped
        FROM products p
        LEFT JOIN price_history ph ON ph.id = (
            SELECT ph2.id FROM price_history ph2
            WHERE ph2.product_id = p.id
            ORDER BY ph2.date_scraped DESC
            LIMIT 1
        )
        WHERE 1=1
    """
    params = []

    if brand:
        query += " AND p.brand = ?"
        params.append(brand)
    if category:
        query += " AND p.category = ?"
        params.append(category)

    query += " ORDER BY p.brand, p.product_name"

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_history(product_id: int = None, days: int = 90):
    """Get price history for a product (or all products) over the given number of days.

    Returns a list of dicts.
    """
    conn = get_connection()
    cur = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    if product_id:
        cur.execute(
            """
            SELECT ph.*, p.brand, p.product_name
            FROM price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE ph.product_id = ? AND ph.date_scraped >= ?
            ORDER BY ph.date_scraped
            """,
            (product_id, cutoff),
        )
    else:
        cur.execute(
            """
            SELECT ph.*, p.brand, p.product_name
            FROM price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE ph.date_scraped >= ?
            ORDER BY ph.date_scraped
            """,
            (cutoff,),
        )

    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_brands_summary():
    """Get summary statistics per brand.

    Returns a list of dicts with brand, product_count, avg_price, min_price, max_price.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            p.brand,
            COUNT(DISTINCT p.id) AS product_count,
            ROUND(AVG(ph.price), 2) AS avg_price,
            ROUND(MIN(ph.price), 2) AS min_price,
            ROUND(MAX(ph.price), 2) AS max_price
        FROM products p
        JOIN price_history ph ON ph.id = (
            SELECT ph2.id FROM price_history ph2
            WHERE ph2.product_id = p.id
            ORDER BY ph2.date_scraped DESC
            LIMIT 1
        )
        GROUP BY p.brand
        ORDER BY p.brand
    """)

    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
