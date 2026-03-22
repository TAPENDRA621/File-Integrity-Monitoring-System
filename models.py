import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

from database import get_connection, init_db
from utils.risk import classify_risk
from utils.time_utils import is_agent_active, utc_now, utc_now_iso


class FIMRepository:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        init_db(self.db_path)

    def register_agent(self, data: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now_iso()
        agent_id = data["agent_id"]
        hostname = data.get("hostname", "unknown-host")
        ip_address = data.get("ip_address", "0.0.0.0")
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT monitored_paths, registered_at_utc FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        existing = cursor.fetchone()

        monitored_paths_payload = data.get("monitored_paths")
        if monitored_paths_payload is None and existing:
            monitored_paths = existing["monitored_paths"]
        else:
            monitored_paths = json.dumps(monitored_paths_payload or [])

        registered_at_utc = data.get("registered_at_utc") or (
            existing["registered_at_utc"] if existing else now
        )
        last_seen_utc = data.get("last_seen_utc", now)

        cursor.execute(
            """
            INSERT INTO agents (agent_id, hostname, ip_address, monitored_paths, registered_at_utc, last_seen_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                hostname=excluded.hostname,
                ip_address=excluded.ip_address,
                monitored_paths=excluded.monitored_paths,
                last_seen_utc=excluded.last_seen_utc
            """,
            (agent_id, hostname, ip_address, monitored_paths, registered_at_utc, last_seen_utc),
        )
        conn.commit()
        conn.close()

        return self.get_agent(agent_id)

    def update_heartbeat(self, agent_id: str, timestamp_utc: Optional[str] = None) -> Optional[Dict[str, Any]]:
        heartbeat_time = timestamp_utc or utc_now_iso()
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE agents SET last_seen_utc = ? WHERE agent_id = ?",
            (heartbeat_time, agent_id),
        )
        changed = cursor.rowcount
        conn.commit()
        conn.close()

        if not changed:
            return None
        return self.get_agent(agent_id)

    def insert_event(self, data: Dict[str, Any]) -> Dict[str, Any]:
        timestamp_utc = data.get("timestamp_utc", utc_now_iso())
        file_path = data.get("file_path", "")
        risk_level = data.get("risk_level") or classify_risk(file_path)

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agents (agent_id, hostname, ip_address, monitored_paths, registered_at_utc, last_seen_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                hostname=excluded.hostname,
                ip_address=excluded.ip_address,
                last_seen_utc=excluded.last_seen_utc
            """,
            (
                data["agent_id"],
                data.get("hostname", "unknown-host"),
                data.get("ip_address", "0.0.0.0"),
                "[]",
                timestamp_utc,
                timestamp_utc,
            ),
        )

        cursor.execute(
            """
            INSERT INTO events (
                agent_id, hostname, ip_address, timestamp_utc,
                file_path, event_type, hash_before, hash_after, risk_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["agent_id"],
                data.get("hostname", "unknown-host"),
                data.get("ip_address", "0.0.0.0"),
                timestamp_utc,
                file_path,
                data.get("event_type", "modified"),
                data.get("hash_before"),
                data.get("hash_after"),
                risk_level,
            ),
        )
        event_id = cursor.lastrowid
        conn.commit()

        cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()

        return dict(row)

    def list_agents(self) -> List[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents ORDER BY last_seen_utc DESC")
        rows = cursor.fetchall()
        conn.close()

        agents = [dict(row) for row in rows]
        for agent in agents:
            agent["status"] = "Active" if is_agent_active(agent.get("last_seen_utc")) else "Inactive"
            agent["monitored_paths"] = json.loads(agent.get("monitored_paths") or "[]")
        return agents

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        agent = dict(row)
        agent["status"] = "Active" if is_agent_active(agent.get("last_seen_utc")) else "Inactive"
        agent["monitored_paths"] = json.loads(agent.get("monitored_paths") or "[]")
        return agent

    def list_events(
        self,
        agent_id: Optional[str] = None,
        risk_level: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []

        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if risk_level:
            clauses.append("risk_level = ?")
            params.append(risk_level.upper())
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type.lower())

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM events {where_sql} ORDER BY timestamp_utc DESC LIMIT ?",
            (*params, int(limit)),
        )
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def dashboard_summary(self) -> Dict[str, Any]:
        now = utc_now()
        npt_offset = timedelta(hours=5, minutes=45)
        now_npt = now + npt_offset
        npt_start = now_npt.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = (npt_start - npt_offset).strftime("%Y-%m-%dT%H:%M:%SZ")
        day_end_utc = (npt_start - npt_offset + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        time_cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

        agents = self.list_agents()
        total_agents = len(agents)
        active_agents = sum(1 for agent in agents if agent["status"] == "Active")
        inactive_agents = total_agents - active_agents

        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) AS c FROM events WHERE timestamp_utc >= ? AND timestamp_utc < ?",
            (day_start_utc, day_end_utc),
        )
        total_events_today = cursor.fetchone()["c"]

        risk_counts = {}
        for risk in ("HIGH", "MEDIUM", "LOW"):
            cursor.execute(
                "SELECT COUNT(*) AS c FROM events WHERE risk_level = ? AND timestamp_utc >= ? AND timestamp_utc < ?",
                (risk, day_start_utc, day_end_utc),
            )
            risk_counts[risk] = cursor.fetchone()["c"]

        cursor.execute(
            "SELECT event_type, COUNT(*) AS c FROM events GROUP BY event_type"
        )
        event_distribution = {row["event_type"]: row["c"] for row in cursor.fetchall()}

        cursor.execute(
            "SELECT timestamp_utc, COUNT(*) AS c FROM events WHERE timestamp_utc >= ? GROUP BY timestamp_utc ORDER BY timestamp_utc ASC",
            (time_cutoff,),
        )
        timeline_rows = cursor.fetchall()

        cursor.execute(
            "SELECT * FROM events ORDER BY timestamp_utc DESC LIMIT 30"
        )
        recent_events = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            "SELECT * FROM events WHERE risk_level = 'HIGH' ORDER BY timestamp_utc DESC LIMIT 10"
        )
        recent_high_risk = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "cards": {
                "total_agents": total_agents,
                "active_agents": active_agents,
                "inactive_agents": inactive_agents,
                "total_events_today": total_events_today,
                "high_risk_events": risk_counts["HIGH"],
                "medium_risk_events": risk_counts["MEDIUM"],
                "low_risk_events": risk_counts["LOW"],
            },
            "event_distribution": event_distribution,
            "risk_distribution": risk_counts,
            "timeline": {
                "labels": [row["timestamp_utc"] for row in timeline_rows],
                "counts": [row["c"] for row in timeline_rows],
            },
            "recent_events": recent_events,
            "recent_high_risk": recent_high_risk,
        }
