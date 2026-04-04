import json
import os
import re
import secrets
import uuid
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
        monitor_status = str(data.get("monitor_status") or "ok").strip().lower() or "ok"
        monitor_message = str(data.get("monitor_message") or "").strip()
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT monitored_paths, registered_at_utc, monitor_status, monitor_message FROM agents WHERE agent_id = ?",
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
        if not monitor_message and existing and existing["monitor_message"]:
            monitor_message = existing["monitor_message"]
        if not monitor_status and existing and existing["monitor_status"]:
            monitor_status = existing["monitor_status"]

        cursor.execute(
            """
            INSERT INTO agents (
                agent_id, hostname, ip_address, monitored_paths,
                registered_at_utc, last_seen_utc, monitor_status, monitor_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                hostname=excluded.hostname,
                ip_address=excluded.ip_address,
                monitored_paths=excluded.monitored_paths,
                last_seen_utc=excluded.last_seen_utc,
                monitor_status=excluded.monitor_status,
                monitor_message=excluded.monitor_message
            """,
            (
                agent_id,
                hostname,
                ip_address,
                monitored_paths,
                registered_at_utc,
                last_seen_utc,
                monitor_status,
                monitor_message,
            ),
        )
        conn.commit()
        conn.close()

        return self.get_agent(agent_id)

    def update_heartbeat(
        self,
        agent_id: str,
        timestamp_utc: Optional[str] = None,
        monitor_status: Optional[str] = None,
        monitor_message: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        heartbeat_time = timestamp_utc or utc_now_iso()
        normalized_status = str(monitor_status or "").strip().lower()
        normalized_message = str(monitor_message or "").strip()

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        if normalized_status or normalized_message:
            cursor.execute(
                """
                UPDATE agents
                SET last_seen_utc = ?, monitor_status = ?, monitor_message = ?
                WHERE agent_id = ?
                """,
                (
                    heartbeat_time,
                    normalized_status or "ok",
                    normalized_message,
                    agent_id,
                ),
            )
        else:
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
        event_type = data.get("event_type", "modified")
        risk_level = data.get("risk_level") or classify_risk(file_path, event_type)
        replayed_offline = 1 if bool(data.get("replayed_offline")) else 0

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
                file_path, event_type, hash_before, hash_after, replayed_offline, risk_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["agent_id"],
                data.get("hostname", "unknown-host"),
                data.get("ip_address", "0.0.0.0"),
                timestamp_utc,
                file_path,
                event_type,
                data.get("hash_before"),
                data.get("hash_after"),
                replayed_offline,
                risk_level,
            ),
        )
        event_id = cursor.lastrowid
        conn.commit()

        cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()

        return self._event_row_to_dict(row)

    @staticmethod
    def _event_row_to_dict(row: Any) -> Dict[str, Any]:
        event = dict(row)
        event["replayed_offline"] = bool(event.get("replayed_offline", 0))
        return event

    @staticmethod
    def _severity_from_event_type(event_type: str) -> str:
        normalized = (event_type or "").lower()
        if normalized == "deleted":
            return "HIGH"
        if normalized == "modified":
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _alert_message(agent_name: str, event_type: str, file_path: str, severity: str) -> str:
        return f"[{severity}] {agent_name} reported {event_type} on {file_path}"

    def create_alert_from_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event_type = (event.get("event_type") or "modified").lower()
        severity = self._severity_from_event_type(event_type)
        timestamp_utc = event.get("timestamp_utc") or utc_now_iso()
        agent_name = event.get("hostname") or event.get("agent_id") or "unknown-agent"
        file_path = event.get("file_path") or ""
        alert_id = f"ALT-{uuid.uuid4().hex[:12].upper()}"
        alert_message = self._alert_message(agent_name, event_type, file_path, severity)

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO alerts (
                alert_id, agent_name, agent_id, file_path,
                event_type, severity, timestamp_utc, alert_message, is_read
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                alert_id,
                agent_name,
                event.get("agent_id", ""),
                file_path,
                event_type,
                severity,
                timestamp_utc,
                alert_message,
            ),
        )
        conn.commit()

        cursor.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
        row = cursor.fetchone()
        conn.close()

        alert = dict(row)
        alert["is_read"] = bool(alert.get("is_read"))
        return alert

    def list_alerts(
        self,
        unread_only: bool = False,
        severity: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 300,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []

        if unread_only:
            clauses.append("is_read = 0")
        if severity:
            clauses.append("severity = ?")
            params.append(severity.upper())
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM alerts {where_sql} ORDER BY timestamp_utc DESC LIMIT ?",
            (*params, int(limit)),
        )
        rows = cursor.fetchall()
        conn.close()

        alerts = [dict(row) for row in rows]
        for alert in alerts:
            alert["is_read"] = bool(alert.get("is_read"))
        return alerts

    def mark_alert_read(self, alert_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE alerts SET is_read = 1 WHERE alert_id = ?",
            (alert_id,),
        )
        changed = cursor.rowcount
        conn.commit()

        if not changed:
            conn.close()
            return None

        cursor.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
        row = cursor.fetchone()
        conn.close()

        alert = dict(row)
        alert["is_read"] = bool(alert.get("is_read"))
        return alert

    def clear_alerts(self, severity: Optional[str] = None, agent_id: Optional[str] = None) -> int:
        clauses = []
        params: List[Any] = []

        if severity:
            clauses.append("severity = ?")
            params.append(severity.upper())
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM alerts {where_sql}", tuple(params))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted

    def alerts_summary(self) -> Dict[str, int]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) AS c FROM alerts")
        total_alerts = cursor.fetchone()["c"]

        cursor.execute("SELECT COUNT(*) AS c FROM alerts WHERE is_read = 0")
        unread_alerts = cursor.fetchone()["c"]

        cursor.execute("SELECT COUNT(*) AS c FROM alerts WHERE severity = 'HIGH'")
        high_severity_alerts = cursor.fetchone()["c"]

        conn.close()

        return {
            "total_alerts": total_alerts,
            "unread_alerts": unread_alerts,
            "high_severity_alerts": high_severity_alerts,
        }

    @staticmethod
    def _normalize_agent_id(raw_value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw_value or "").strip())
        cleaned = cleaned.strip("-")
        return cleaned.lower()

    @staticmethod
    def _clean_monitor_path_token(value: Any) -> str:
        token = str(value or "").strip()
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
            token = token[1:-1].strip()
        return os.path.expandvars(os.path.expanduser(token))

    @staticmethod
    def _parse_positive_int(value: Any, default_value: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default_value
        except (TypeError, ValueError):
            return default_value

    @staticmethod
    def _normalize_monitor_paths(raw_paths: Any) -> List[str]:
        if isinstance(raw_paths, list):
            tokens = [str(item).strip() for item in raw_paths]
        else:
            text = str(raw_paths or "")
            text = text.replace("\r\n", "\n").replace(";", "\n").replace(",", "\n")
            tokens = [item.strip() for item in text.split("\n")]

        paths = [FIMRepository._clean_monitor_path_token(path) for path in tokens if path]
        deduped: List[str] = []
        seen = set()
        for path in paths:
            normalized_key = os.path.normcase(os.path.normpath(path))
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            deduped.append(path)
        return deduped

    def _ensure_unique_agent_id(self, preferred_id: str) -> str:
        base_id = self._normalize_agent_id(preferred_id) or f"agent-{uuid.uuid4().hex[:8]}"
        candidate = base_id
        suffix = 1

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        while True:
            cursor.execute("SELECT 1 FROM agent_profiles WHERE agent_id = ?", (candidate,))
            found = cursor.fetchone()
            if not found:
                break
            suffix += 1
            candidate = f"{base_id}-{suffix}"
        conn.close()
        return candidate

    def _profile_status(self, enabled: bool, last_seen_utc: Optional[str], last_enrolled_utc: Optional[str]) -> str:
        if not enabled:
            return "Disabled"
        if last_seen_utc:
            return "Active" if is_agent_active(last_seen_utc) else "Offline"
        if last_enrolled_utc:
            return "Pending"
        return "Not Installed"

    def _hydrate_profile_row(self, row: Any) -> Dict[str, Any]:
        profile = dict(row)
        profile["monitor_paths"] = json.loads(profile.get("monitor_paths") or "[]")
        profile["enabled"] = bool(profile.get("enabled", 0))
        profile["status"] = self._profile_status(
            profile["enabled"],
            profile.get("last_seen_utc"),
            profile.get("last_enrolled_utc"),
        )
        return profile

    def create_agent_profile(self, data: Dict[str, Any]) -> Dict[str, Any]:
        agent_name = str(data.get("agent_name") or "").strip()
        if not agent_name:
            raise ValueError("agent_name is required")

        explicit_agent_id = self._normalize_agent_id(str(data.get("agent_id") or ""))
        preferred_agent_id = explicit_agent_id or self._normalize_agent_id(agent_name)
        agent_id = self._ensure_unique_agent_id(preferred_agent_id)

        monitor_paths = self._normalize_monitor_paths(data.get("monitor_paths"))
        if not monitor_paths:
            raise ValueError("monitor_paths must include at least one path")

        heartbeat_seconds = self._parse_positive_int(data.get("heartbeat_seconds"), 30)
        poll_seconds = self._parse_positive_int(data.get("poll_seconds"), 15)
        risk_label = str(data.get("risk_label") or "").strip()
        enrollment_token = secrets.token_urlsafe(24)
        now = utc_now_iso()

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_profiles (
                agent_id, agent_name, monitor_paths,
                heartbeat_seconds, poll_seconds, risk_label,
                enrollment_token, enabled, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                agent_id,
                agent_name,
                json.dumps(monitor_paths),
                heartbeat_seconds,
                poll_seconds,
                risk_label,
                enrollment_token,
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        return self.get_agent_profile(agent_id)

    def list_agent_profiles(self) -> List[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.*, a.hostname, a.ip_address, a.last_seen_utc
            FROM agent_profiles p
            LEFT JOIN agents a ON a.agent_id = p.agent_id
            ORDER BY p.created_at_utc DESC
            """
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._hydrate_profile_row(row) for row in rows]

    def get_agent_profile(self, agent_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.*, a.hostname, a.ip_address, a.last_seen_utc
            FROM agent_profiles p
            LEFT JOIN agents a ON a.agent_id = p.agent_id
            WHERE p.agent_id = ?
            """,
            (agent_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return self._hydrate_profile_row(row)

    def update_agent_profile(self, agent_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        existing = self.get_agent_profile(agent_id)
        if not existing:
            return None

        agent_name = str(data.get("agent_name") or existing["agent_name"]).strip()
        monitor_paths = self._normalize_monitor_paths(data.get("monitor_paths", existing["monitor_paths"]))
        if not monitor_paths:
            raise ValueError("monitor_paths must include at least one path")

        heartbeat_seconds = self._parse_positive_int(data.get("heartbeat_seconds", existing["heartbeat_seconds"]), 30)
        poll_seconds = self._parse_positive_int(data.get("poll_seconds", existing["poll_seconds"]), 15)
        risk_label = str(data.get("risk_label", existing.get("risk_label", ""))).strip()
        enabled = 1 if bool(data.get("enabled", existing.get("enabled", True))) else 0
        now = utc_now_iso()

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE agent_profiles
            SET agent_name = ?,
                monitor_paths = ?,
                heartbeat_seconds = ?,
                poll_seconds = ?,
                risk_label = ?,
                enabled = ?,
                updated_at_utc = ?
            WHERE agent_id = ?
            """,
            (
                agent_name,
                json.dumps(monitor_paths),
                heartbeat_seconds,
                poll_seconds,
                risk_label,
                enabled,
                now,
                agent_id,
            ),
        )
        conn.commit()
        conn.close()
        return self.get_agent_profile(agent_id)

    def set_agent_profile_enabled(self, agent_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE agent_profiles SET enabled = ?, updated_at_utc = ? WHERE agent_id = ?",
            (1 if enabled else 0, now, agent_id),
        )
        changed = cursor.rowcount
        conn.commit()
        conn.close()
        if not changed:
            return None
        return self.get_agent_profile(agent_id)

    def regenerate_agent_profile_token(self, agent_id: str) -> Optional[Dict[str, Any]]:
        new_token = secrets.token_urlsafe(24)
        now = utc_now_iso()
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE agent_profiles SET enrollment_token = ?, updated_at_utc = ? WHERE agent_id = ?",
            (new_token, now, agent_id),
        )
        changed = cursor.rowcount
        conn.commit()
        conn.close()
        if not changed:
            return None
        return self.get_agent_profile(agent_id)

    def delete_agent_profile(self, agent_id: str) -> bool:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM agent_profiles WHERE agent_id = ?", (agent_id,))
        changed = cursor.rowcount
        conn.commit()
        conn.close()
        return bool(changed)

    def get_agent_profile_by_token(self, enrollment_token: str) -> Optional[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.*, a.hostname, a.ip_address, a.last_seen_utc
            FROM agent_profiles p
            LEFT JOIN agents a ON a.agent_id = p.agent_id
            WHERE p.enrollment_token = ?
            """,
            (enrollment_token,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return self._hydrate_profile_row(row)

    def get_enabled_agent_profile_by_name(self, agent_name: str) -> Optional[Dict[str, Any]]:
        target_name = str(agent_name or "").strip()
        if not target_name:
            return None

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.*, a.hostname, a.ip_address, a.last_seen_utc
            FROM agent_profiles p
            LEFT JOIN agents a ON a.agent_id = p.agent_id
            WHERE lower(p.agent_name) = lower(?) AND p.enabled = 1
            ORDER BY p.updated_at_utc DESC
            LIMIT 2
            """,
            (target_name,),
        )
        rows = cursor.fetchall()
        conn.close()

        if len(rows) != 1:
            return None
        return self._hydrate_profile_row(rows[0])

    def mark_profile_enrolled(self, agent_id: str) -> None:
        now = utc_now_iso()
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE agent_profiles SET last_enrolled_utc = ?, updated_at_utc = ? WHERE agent_id = ?",
            (now, now, agent_id),
        )
        conn.commit()
        conn.close()

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

        return [self._event_row_to_dict(row) for row in rows]

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
        recent_events = [self._event_row_to_dict(row) for row in cursor.fetchall()]

        cursor.execute(
            "SELECT * FROM events WHERE risk_level = 'HIGH' ORDER BY timestamp_utc DESC LIMIT 10"
        )
        recent_high_risk = [self._event_row_to_dict(row) for row in cursor.fetchall()]

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
