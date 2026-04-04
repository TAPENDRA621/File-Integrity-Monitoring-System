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

    def ensure_column(table_name: str, column_name: str, column_def: str) -> None:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in cursor.fetchall()}
        if column_name not in columns:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            hostname TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            monitored_paths TEXT DEFAULT '',
            registered_at_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL,
            monitor_status TEXT DEFAULT 'ok',
            monitor_message TEXT DEFAULT ''
        )
        """
    )

    ensure_column("agents", "monitor_status", "TEXT DEFAULT 'ok'")
    ensure_column("agents", "monitor_message", "TEXT DEFAULT ''")

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
            replayed_offline INTEGER NOT NULL DEFAULT 0,
            risk_level TEXT NOT NULL,
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
        )
        """
    )

    ensure_column("events", "replayed_offline", "INTEGER NOT NULL DEFAULT 0")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id TEXT NOT NULL UNIQUE,
            agent_name TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            alert_message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL UNIQUE,
            agent_name TEXT NOT NULL,
            monitor_paths TEXT NOT NULL,
            heartbeat_seconds INTEGER NOT NULL DEFAULT 30,
            poll_seconds INTEGER NOT NULL DEFAULT 15,
            risk_label TEXT DEFAULT '',
            enrollment_token TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            last_enrolled_utc TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
        )
        """
    )

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_utc DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_risk ON events(risk_level)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp_utc DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_agent ON alerts(agent_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_is_read ON alerts(is_read)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_profiles_agent_id ON agent_profiles(agent_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_profiles_token ON agent_profiles(enrollment_token)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_profiles_enabled ON agent_profiles(enabled)")

    conn.commit()
    conn.close()
