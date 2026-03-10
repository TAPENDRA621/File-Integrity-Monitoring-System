import os
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque
from typing import Optional

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_socketio import SocketIO


# =============================================================================
# Configuration
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "db", "fims.db")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

FIMS_API_KEY = os.environ.get("FIMS_API_KEY", "secret-fims-key")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
PORT = int(os.environ.get("PORT", "5000"))


# =============================================================================
# Logging (structured, production-friendly)
# =============================================================================

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("FIMS_SERVER")


# =============================================================================
# Flask & Socket.IO initialization
# =============================================================================

app = Flask(__name__, template_folder=TEMPLATES_DIR)
app.config["SECRET_KEY"] = SECRET_KEY

# Allow remote agents and dashboards (CORS for API & websockets)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# eventlet async mode is required when running under Gunicorn with -k eventlet
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
)


# =============================================================================
# Database Setup
# =============================================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            timestamp TEXT,
            severity TEXT,
            event_type TEXT,
            file_path TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_FILE)


init_db()


# =============================================================================
# Analytics Engine (in-memory aggregation)
# =============================================================================

class AnalyticsEngine:
    """
    Maintains:
      - cumulative counters
      - last-24h time series (minute buckets)
    """

    def __init__(self, window_hours: int = 24):
        self.window = timedelta(hours=window_hours)

        # Counters
        self.total_events = 0
        self.files_added = 0
        self.files_modified = 0
        self.files_deleted = 0

        # Time series: deque of (timestamp, event_type)
        self.events_timeline = deque()

    def _prune_old_events(self, now: datetime):
        """Remove events older than the sliding window."""
        cutoff = now - self.window
        while self.events_timeline and self.events_timeline[0][0] < cutoff:
            self.events_timeline.popleft()

    def register_event(self, event_type: str, timestamp: Optional[datetime] = None):
        now = timestamp or datetime.now(timezone.utc)
        self._prune_old_events(now)

        self.total_events += 1
        if event_type == "FILE_ADDED":
            self.files_added += 1
        elif event_type == "FILE_MODIFIED":
            self.files_modified += 1
        elif event_type == "FILE_DELETED":
            self.files_deleted += 1

        self.events_timeline.append((now, event_type))

    def get_counters(self) -> dict:
        return {
            "total_events": self.total_events,
            "files_added": self.files_added,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
        }

    def get_distribution(self) -> dict:
        dist = defaultdict(int)
        for _, etype in self.events_timeline:
            dist[etype] += 1
        return dict(dist)

    def get_time_series(self) -> dict:
        """
        Returns per-minute counts for the last 24 hours:
          {
            "labels": [... ISO minute strings ...],
            "counts": [... total events per minute ...]
          }
        """
        if not self.events_timeline:
            return {"labels": [], "counts": []}

        # Create minute buckets
        buckets = defaultdict(int)
        for ts, _ in self.events_timeline:
            minute_ts = ts.replace(second=0, microsecond=0)
            buckets[minute_ts] += 1

        # Build ordered series (oldest to newest)
        all_minutes = sorted(buckets.keys())
        labels = [m.isoformat() for m in all_minutes]
        counts = [buckets[m] for m in all_minutes]

        return {"labels": labels, "counts": counts}

    def snapshot(self) -> dict:
        """Combined analytics payload."""
        return {
            "counters": self.get_counters(),
            "distribution": self.get_distribution(),
            "time_series": self.get_time_series(),
        }


analytics = AnalyticsEngine()


# =============================================================================
# Helpers
# =============================================================================

def verify_api_key(req: request) -> bool:
    api_key = req.headers.get("x-api-key")
    if not api_key or api_key != FIMS_API_KEY:
        logger.warning("Unauthorized request from %s", req.remote_addr)
        return False
    return True


def row_to_dict(row):
    return {
        "id": row[0],
        "agent_name": row[1],
        "timestamp": row[2],
        "severity": row[3],
        "event_type": row[4],
        "file_path": row[5],
    }


# =============================================================================
# Web Routes (Dashboard)
# =============================================================================

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


# =============================================================================
# API Routes (REST)
# =============================================================================

@app.route("/api/logs", methods=["POST"])
def receive_logs():
    if not verify_api_key(request):
        return jsonify({"detail": "Invalid API Key"}), 401

    data = request.get_json(silent=True) or {}
    agent_name = data.get("agent_name") or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    severity = data.get("severity") or "INFO"
    event_type = data.get("event_type") or "UNKNOWN"
    file_path = data.get("file_path") or ""

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO logs (agent_name, timestamp, severity, event_type, file_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (agent_name, timestamp, severity, event_type, file_path),
    )
    conn.commit()

    c.execute(
        "SELECT id, agent_name, timestamp, severity, event_type, file_path "
        "FROM logs WHERE id = last_insert_rowid()"
    )
    log_row = c.fetchone()
    conn.close()

    log_dict = row_to_dict(log_row)

    # Update analytics and broadcast over Socket.IO
    analytics.register_event(event_type)
    payload = {
        "log": log_dict,
        "analytics": analytics.snapshot(),
    }

    # Broadcast to all connected dashboard clients.
    # Newer python-socketio removed the `broadcast` kwarg; emitting without a
    # target sends to all clients by default.
    socketio.emit("log_event", payload)
    logger.info(
        "Received log from agent=%s type=%s path=%s",
        agent_name,
        event_type,
        file_path,
    )

    return jsonify({"status": "success"})


@app.route("/api/logs", methods=["GET"])
def get_logs():
    """Return recent logs for HTTP clients (viewer, fallback polling, etc.)."""
    limit = int(request.args.get("limit", "100"))
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT id, agent_name, timestamp, severity, event_type, file_path "
        "FROM logs ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()

    data = [row_to_dict(row) for row in rows]
    return jsonify(data)


@app.route("/api/analytics", methods=["GET"])
def get_analytics():
    """Expose current analytics snapshot over HTTP (optional)."""
    return jsonify(analytics.snapshot())


@app.route("/api/report", methods=["GET"])
def report():
    """
    Existing report endpoint used by the desktop viewer.
    Kept simple: returns all logs as JSON.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()

    data = [dict(row) for row in rows]
    return jsonify(data)


# =============================================================================
# Socket.IO Events
# =============================================================================

@socketio.on("connect")
def handle_connect():
    logger.info("Dashboard client connected: %s", request.sid)

    # On connect, send initial logs & analytics snapshot
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT id, agent_name, timestamp, severity, event_type, file_path "
        "FROM logs ORDER BY id DESC LIMIT 100"
    )
    rows = c.fetchall()
    conn.close()

    logs = [row_to_dict(row) for row in rows]

    socketio.emit(
        "init_data",
        {
            "logs": logs,
            "analytics": analytics.snapshot(),
        },
        room=request.sid,
    )


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("Dashboard client disconnected: %s", request.sid)


# =============================================================================
# Local Development Entry Point (not used in Gunicorn)
# =============================================================================

if __name__ == "__main__":
    # No debug=True – production-like settings even for local runs.
    logger.info("Starting FIMS server on 0.0.0.0:%d", PORT)
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)

