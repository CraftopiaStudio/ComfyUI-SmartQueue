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
CREATE TABLE IF NOT EXISTS manual_pause_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    paused INTEGER NOT NULL
);
"""

# Columns added after the tables above already shipped (spec §29/§6/#3) —
# CREATE TABLE IF NOT EXISTS never touches a table that already exists, so an
# existing smart_queue.sqlite3 needs these added explicitly or it keeps
# missing them forever.
_ADDED_COLUMNS = {
    "queue_items": [("started_at", "TEXT")],
    "history": [("workflow_json", "TEXT"), ("duration_seconds", "REAL")],
}


def _migrate_schema(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, col_type in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
    conn.commit()


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    _migrate_schema(conn)
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


def rename_queue_item(conn: sqlite3.Connection, prompt_id: str, name: str) -> None:
    conn.execute("UPDATE queue_items SET name = ? WHERE prompt_id = ?", (name, prompt_id))
    conn.commit()


def list_queue_items(
    conn: sqlite3.Connection,
    name_contains: str | None = None,
) -> list[dict]:
    query = "SELECT * FROM queue_items"
    clauses: list[str] = []
    params: list = []
    if name_contains:
        clauses.append("LOWER(name) LIKE ?")
        params.append(f"%{name_contains.lower()}%")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY order_index ASC"
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def reorder_queue_items(conn: sqlite3.Connection, ordered_prompt_ids: list[str]) -> None:
    for index, prompt_id in enumerate(ordered_prompt_ids):
        conn.execute("UPDATE queue_items SET order_index = ? WHERE prompt_id = ?", (index, prompt_id))
    conn.commit()


def remove_queue_item(conn: sqlite3.Connection, prompt_id: str) -> None:
    conn.execute("DELETE FROM queue_items WHERE prompt_id = ?", (prompt_id,))
    conn.commit()


def mark_queue_item_started(conn: sqlite3.Connection, prompt_id: str) -> None:
    conn.execute(
        "UPDATE queue_items SET started_at = ? WHERE prompt_id = ?",
        (datetime.now(timezone.utc).isoformat(), prompt_id),
    )
    conn.commit()


def mark_completed(
    conn: sqlite3.Connection,
    prompt_id: str,
    thumbnail_path: str | None = None,
    workflow_json: str | None = None,
) -> None:
    row = conn.execute("SELECT name, started_at FROM queue_items WHERE prompt_id = ?", (prompt_id,)).fetchone()
    name = row["name"] if row else prompt_id
    completed_at = datetime.now(timezone.utc)
    duration_seconds = None
    if row and row["started_at"]:
        started_at = datetime.fromisoformat(row["started_at"])
        duration_seconds = (completed_at - started_at).total_seconds()
    conn.execute(
        "INSERT INTO history (prompt_id, name, thumbnail_path, workflow_json, duration_seconds, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (prompt_id, name, thumbnail_path, workflow_json, duration_seconds, completed_at.isoformat()),
    )
    conn.execute("DELETE FROM queue_items WHERE prompt_id = ?", (prompt_id,))
    conn.commit()


def list_history(
    conn: sqlite3.Connection,
    limit: int = 50,
    name_contains: str | None = None,
) -> list[dict]:
    query = "SELECT * FROM history"
    clauses: list[str] = []
    params: list = []
    if name_contains:
        clauses.append("LOWER(name) LIKE ?")
        params.append(f"%{name_contains.lower()}%")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY completed_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def delete_history_older_than(conn: sqlite3.Connection, cutoff_iso: str) -> int:
    cursor = conn.execute("DELETE FROM history WHERE completed_at < ?", (cutoff_iso,))
    conn.commit()
    return cursor.rowcount


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


def save_manual_pause(conn: sqlite3.Connection, paused: bool) -> None:
    """Persists the manual-pause flag so a restart doesn't silently resume a
    queue the user deliberately paused, even when nothing was in flight to
    hold (spec §29 #11) — held_items alone can't cover that case since it's
    only ever non-empty while something was actually queued during the pause."""
    conn.execute(
        "INSERT INTO manual_pause_state (id, paused) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET paused = excluded.paused",
        (int(paused),),
    )
    conn.commit()


def load_manual_pause(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT paused FROM manual_pause_state WHERE id = 1").fetchone()
    return bool(row["paused"]) if row else False
