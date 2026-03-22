import os
import sqlite3
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "database", "fims.db")


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("FIMS_DB_PATH", DEFAULT_DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            hostname TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            monitored_paths TEXT DEFAULT '',
            registered_at_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            hostname TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            file_path TEXT NOT NULL,
            event_type TEXT NOT NULL,
            hash_before TEXT,
            hash_after TEXT,
            risk_level TEXT NOT NULL,
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
        )
        """
    )

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_utc DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_risk ON events(risk_level)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")

    conn.commit()
    conn.close()
