"""
SQLite-backed session store.

Schema
──────
sessions  — one row per (node_id, channel) pair, holds the rolling summary
messages  — ordered conversation turns that belong to a session

All writes go through the single LLM worker thread, so no extra locking is
needed beyond what the gateway already holds.
"""

import sqlite3
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    node_id    TEXT    NOT NULL,
    channel    INTEGER NOT NULL,
    summary    TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (node_id, channel)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    TEXT    NOT NULL,
    channel    INTEGER NOT NULL,
    role       TEXT    NOT NULL,   -- 'user' | 'assistant'
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id, channel) REFERENCES sessions (node_id, channel)
);
"""


class SessionStore:
    def __init__(self, db_path: str = "db/sessions.db"):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        # Keep a single connection alive — critical for :memory: (each new
        # connection would get a blank database) and efficient for file DBs.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    # ── internal ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        return self._conn

    def _init_db(self):
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _ensure_session(self, conn: sqlite3.Connection, node_id: str, channel: int):
        conn.execute(
            "INSERT OR IGNORE INTO sessions (node_id, channel) VALUES (?, ?)",
            (node_id, channel),
        )

    # ── public API ────────────────────────────────────────────────────────────

    def load_session(self, node_id: str, channel: int) -> dict:
        """Return {"history": [(role, content), …], "summary": str} from DB."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary FROM sessions WHERE node_id=? AND channel=?",
                (node_id, channel),
            ).fetchone()
            summary = row["summary"] if row else ""

            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE node_id=? AND channel=? ORDER BY id",
                (node_id, channel),
            ).fetchall()
            history = [(r["role"], r["content"]) for r in rows]

        return {"history": history, "summary": summary}

    def append_messages(self, node_id: str, channel: int, turns: list[tuple[str, str]]):
        """Append one or more (role, content) turns to the session."""
        with self._connect() as conn:
            self._ensure_session(conn, node_id, channel)
            conn.executemany(
                "INSERT INTO messages (node_id, channel, role, content) VALUES (?, ?, ?, ?)",
                [(node_id, channel, role, content) for role, content in turns],
            )
            conn.execute(
                "UPDATE sessions SET updated_at=datetime('now') WHERE node_id=? AND channel=?",
                (node_id, channel),
            )

    def replace_history(self, node_id: str, channel: int, history: list[tuple[str, str]], summary: str):
        """
        Replace all messages for a session and update its summary.
        Used after history compression — old turns are gone, kept turns are re-inserted.
        """
        with self._connect() as conn:
            self._ensure_session(conn, node_id, channel)
            conn.execute(
                "DELETE FROM messages WHERE node_id=? AND channel=?",
                (node_id, channel),
            )
            conn.executemany(
                "INSERT INTO messages (node_id, channel, role, content) VALUES (?, ?, ?, ?)",
                [(node_id, channel, role, content) for role, content in history],
            )
            conn.execute(
                "UPDATE sessions SET summary=?, updated_at=datetime('now') WHERE node_id=? AND channel=?",
                (summary, node_id, channel),
            )

    def delete_session(self, node_id: str, channel: int):
        """Wipe all history and summary for a session (called on !reset)."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM messages WHERE node_id=? AND channel=?",
                (node_id, channel),
            )
            conn.execute(
                "DELETE FROM sessions WHERE node_id=? AND channel=?",
                (node_id, channel),
            )
