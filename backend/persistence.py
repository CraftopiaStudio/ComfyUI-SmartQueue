"""SQLite-backed queue order + history. Survives ComfyUI restarts."""

import json
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    order_index INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id TEXT NOT NULL,
    name TEXT NOT NULL,
    thumbnail_path TEXT,
    completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS held_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_index INTEGER NOT NULL,
    item_json TEXT NOT NULL
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def add_queue_item(conn: sqlite3.Connection, prompt_id: str, name: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(order_index), -1) + 1 AS next FROM queue_items").fetchone()
    next_index = row["next"]
    cursor = conn.execute(
        "INSERT INTO queue_items (prompt_id, name, status, order_index, created_at) VALUES (?, ?, 'pending', ?, ?)",
        (prompt_id, name, next_index, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


def set_queue_item_status(conn: sqlite3.Connection, prompt_id: str, status: str) -> None:
    conn.execute("UPDATE queue_items SET status = ? WHERE prompt_id = ?", (status, prompt_id))
    conn.commit()


def list_queue_items(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM queue_items ORDER BY order_index ASC").fetchall()
    return [dict(row) for row in rows]


def reorder_queue_items(conn: sqlite3.Connection, ordered_prompt_ids: list[str]) -> None:
    for index, prompt_id in enumerate(ordered_prompt_ids):
        conn.execute("UPDATE queue_items SET order_index = ? WHERE prompt_id = ?", (index, prompt_id))
    conn.commit()


def remove_queue_item(conn: sqlite3.Connection, prompt_id: str) -> None:
    conn.execute("DELETE FROM queue_items WHERE prompt_id = ?", (prompt_id,))
    conn.commit()


def mark_completed(conn: sqlite3.Connection, prompt_id: str, thumbnail_path: str | None = None) -> None:
    row = conn.execute("SELECT name FROM queue_items WHERE prompt_id = ?", (prompt_id,)).fetchone()
    name = row["name"] if row else prompt_id
    conn.execute(
        "INSERT INTO history (prompt_id, name, thumbnail_path, completed_at) VALUES (?, ?, ?, ?)",
        (prompt_id, name, thumbnail_path, datetime.now(timezone.utc).isoformat()),
    )
    conn.execute("DELETE FROM queue_items WHERE prompt_id = ?", (prompt_id,))
    conn.commit()


def list_history(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM history ORDER BY completed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def save_held_items(conn: sqlite3.Connection, items: list[tuple]) -> None:
    """Replaces the entire held-items snapshot. Called after every hold/release
    so a crash or restart mid-pause can recover exactly what was in flight —
    items are raw PromptQueue tuples (number, prompt_id, prompt, extra_data,
    outputs_to_execute, sensitive), JSON-serialized as-is."""
    conn.execute("DELETE FROM held_items")
    for index, item in enumerate(items):
        conn.execute(
            "INSERT INTO held_items (order_index, item_json) VALUES (?, ?)",
            (index, json.dumps(list(item))),
        )
    conn.commit()


def load_held_items(conn: sqlite3.Connection) -> list[tuple]:
    rows = conn.execute("SELECT item_json FROM held_items ORDER BY order_index ASC").fetchall()
    return [tuple(json.loads(row["item_json"])) for row in rows]
