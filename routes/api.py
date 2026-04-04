import csv
import io
import json
import os
import re
from datetime import timedelta

from flask import Blueprint, Response, current_app, jsonify, request

from utils.time_utils import parse_utc_timestamp
from utils.deployment import build_agent_config, build_agent_package, build_install_command

api_bp = Blueprint("api", __name__, url_prefix="/api")


def repo():
    return current_app.config["repo"]


def socketio_instance():
    return current_app.extensions["socketio"]


def _agent_store_path() -> str:
    return current_app.config["agent_store_path"]


def _load_agent_store() -> dict:
    path = _agent_store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _save_agent_store(data: dict) -> None:
    path = _agent_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def _normalize_agent_id(raw_value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw_value or "").strip())
    cleaned = cleaned.strip("-")
    return cleaned.lower()


def _public_server_base_url() -> str:
    configured = os.environ.get("FIMS_AGENT_SERVER_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return request.host_url.rstrip("/")


def _public_server_base_urls() -> list[str]:
    values = []
    primary = _public_server_base_url()
    if primary:
        values.append(primary)

    configured_list = os.environ.get("FIMS_AGENT_SERVER_BASE_URLS", "")
    if configured_list:
        for token in re.split(r"[\r\n,;]+", configured_list):
            cleaned = token.strip().rstrip("/")
            if cleaned:
                values.append(cleaned)

    deduped = []
    seen = set()
    for value in values:
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(value)
    return deduped


def _deployment_payload(raw_payload: dict) -> dict:
    return {
        "agent_name": str(raw_payload.get("agent_name") or "").strip(),
        "agent_id": str(raw_payload.get("agent_id") or "").strip(),
        "monitor_paths": raw_payload.get("monitor_paths"),
        "heartbeat_seconds": raw_payload.get("heartbeat_seconds"),
        "poll_seconds": raw_payload.get("poll_seconds"),
        "risk_label": str(raw_payload.get("risk_label") or "").strip(),
    }


def to_npt(utc_value: str) -> str:
    parsed = parse_utc_timestamp(utc_value)
    if not parsed:
        return ""
    npt_time = parsed + timedelta(hours=5, minutes=45)
    return npt_time.strftime("%Y-%m-%d %H:%M:%S NPT")


def event_filters_from_request(default_limit: int = 500):
    limit = int(request.args.get("limit", default_limit))
    agent_id = request.args.get("agent_id")
    risk_level = request.args.get("risk_level")
    event_type = request.args.get("event_type")
    return limit, agent_id, risk_level, event_type


def events_to_csv_response(rows, filename: str) -> Response:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "agent_id",
            "hostname",
            "ip_address",
            "timestamp_utc",
            "timestamp_npt",
            "file_path",
            "event_type",
            "replayed_offline",
            "risk_level",
            "hash_before",
            "hash_after",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row.get("id"),
                row.get("agent_id"),
                row.get("hostname"),
                row.get("ip_address"),
                row.get("timestamp_utc"),
                to_npt(row.get("timestamp_utc")),
                row.get("file_path"),
                row.get("event_type"),
                "yes" if row.get("replayed_offline") else "no",
                row.get("risk_level"),
                row.get("hash_before") or "",
                row.get("hash_after") or "",
            ]
        )

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@api_bp.post("/agents/register")
def register_agent():
    payload = request.get_json(silent=True) or {}
    enrollment_token = str(payload.get("enrollment_token") or "").strip()

    requested_agent_id = str(payload.get("agent_id") or "").strip()
    requested_agent_name = str(payload.get("agent_name") or payload.get("hostname") or "").strip()

    profile = None
    if enrollment_token:
        profile = repo().get_agent_profile_by_token(enrollment_token)
        if not profile:
            # Token may be stale after profile regeneration; allow same-agent fallback.
            if requested_agent_id:
                profile = repo().get_agent_profile(requested_agent_id)
                if not profile:
                    normalized_agent_id = _normalize_agent_id(requested_agent_id)
                    if normalized_agent_id and normalized_agent_id != requested_agent_id:
                        profile = repo().get_agent_profile(normalized_agent_id)
            if not profile and requested_agent_name:
                profile = repo().get_enabled_agent_profile_by_name(requested_agent_name)
            if not profile:
                return jsonify({"error": "Invalid enrollment token"}), 401
        if not profile.get("enabled", False):
            return jsonify({"error": "Agent profile is disabled"}), 403
    else:
        if requested_agent_id:
            profile = repo().get_agent_profile(requested_agent_id)
            if not profile:
                normalized_agent_id = _normalize_agent_id(requested_agent_id)
                if normalized_agent_id and normalized_agent_id != requested_agent_id:
                    profile = repo().get_agent_profile(normalized_agent_id)
        if not profile and requested_agent_name:
            profile = repo().get_enabled_agent_profile_by_name(requested_agent_name)
            if profile and not profile.get("enabled", False):
                return jsonify({"error": "Agent profile is disabled"}), 403

    agent_id = payload.get("agent_id") or _normalize_agent_id(payload.get("agent_name", ""))
    if profile:
        agent_id = profile["agent_id"]

    if not agent_id:
        return jsonify({"error": "agent_id is required"}), 400

    normalized_payload = dict(payload)
    normalized_payload["agent_id"] = agent_id

    if profile:
        normalized_payload["monitored_paths"] = profile.get("monitor_paths", [])
        if not normalized_payload.get("agent_name"):
            normalized_payload["agent_name"] = profile.get("agent_name")

    if payload.get("agent_name") and not payload.get("hostname"):
        normalized_payload["hostname"] = payload["agent_name"]

    if profile and not normalized_payload.get("hostname"):
        normalized_payload["hostname"] = profile.get("agent_name") or profile.get("agent_id")

    if payload.get("agent_ip_address") and not payload.get("ip_address"):
        normalized_payload["ip_address"] = payload["agent_ip_address"]

    saved = repo().register_agent(normalized_payload)
    if profile:
        repo().mark_profile_enrolled(profile["agent_id"])

    metadata = _load_agent_store()
    existing_meta = metadata.get(agent_id, {})
    metadata[agent_id] = {
        "agent_name": (profile.get("agent_name") if profile else None) or payload.get("agent_name") or existing_meta.get("agent_name") or saved.get("hostname"),
        "ip_address": payload.get("agent_ip_address") or payload.get("ip_address") or existing_meta.get("ip_address") or saved.get("ip_address"),
        "port": str(payload.get("port") or existing_meta.get("port") or ""),
    }
    _save_agent_store(metadata)

    merged_agent = dict(saved)
    merged_agent.update(metadata.get(agent_id, {}))

    socketio_instance().emit("agent:update", merged_agent)
    return jsonify({"status": "registered", "agent": merged_agent})


@api_bp.post("/agents/heartbeat")
def heartbeat():
    payload = request.get_json(silent=True) or {}
    agent_id = payload.get("agent_id")
    if not agent_id:
        return jsonify({"error": "agent_id is required"}), 400

    updated = repo().update_heartbeat(
        agent_id,
        payload.get("timestamp_utc"),
        payload.get("monitor_status"),
        payload.get("monitor_message"),
    )
    if not updated:
        return jsonify({"error": "agent not found"}), 404

    socketio_instance().emit("agent:update", updated)
    return jsonify({"status": "heartbeat_received", "agent": updated})


@api_bp.post("/events")
def create_event():
    payload = request.get_json(silent=True) or {}
    required_fields = ["agent_id", "hostname", "ip_address", "file_path", "event_type"]
    missing = [field for field in required_fields if not payload.get(field)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    payload["event_type"] = payload["event_type"].lower()
    if payload["event_type"] not in {"created", "modified", "deleted"}:
        return jsonify({"error": "event_type must be one of created, modified, deleted"}), 400

    event = repo().insert_event(payload)
    alert = repo().create_alert_from_event(event)
    summary = repo().dashboard_summary()
    alert_counts = repo().alerts_summary()

    socketio_instance().emit("event:new", event)
    socketio_instance().emit("alert:new", alert)
    socketio_instance().emit("alerts:update", alert_counts)
    socketio_instance().emit("dashboard:update", summary)

    return jsonify({"status": "event_stored", "event": event, "alert": alert}), 201


@api_bp.get("/agents")
def get_agents():
    agents = repo().list_agents()
    metadata = _load_agent_store()

    merged_agents = []
    for agent in agents:
        extra = metadata.get(agent["agent_id"], {})
        merged = dict(agent)
        merged["agent_name"] = extra.get("agent_name") or agent.get("hostname")
        merged["port"] = extra.get("port", "")
        if extra.get("ip_address"):
            merged["ip_address"] = extra["ip_address"]
        merged_agents.append(merged)

    return jsonify(merged_agents)


@api_bp.get("/agents/<agent_id>")
def get_agent(agent_id: str):
    agent = repo().get_agent(agent_id)
    if not agent:
        return jsonify({"error": "agent not found"}), 404
    return jsonify(agent)


@api_bp.get("/agents/<agent_id>/events")
def get_agent_events(agent_id: str):
    limit = int(request.args.get("limit", 300))
    risk_level = request.args.get("risk_level")
    event_type = request.args.get("event_type")

    events = repo().list_events(
        agent_id=agent_id,
        risk_level=risk_level,
        event_type=event_type,
        limit=limit,
    )
    return jsonify(events)


@api_bp.get("/agents/<agent_id>/logs")
def get_agent_logs(agent_id: str):
    limit = int(request.args.get("limit", 300))
    risk_level = request.args.get("risk_level")
    event_type = request.args.get("event_type")

    logs = repo().list_events(
        agent_id=agent_id,
        risk_level=risk_level,
        event_type=event_type,
        limit=limit,
    )
    return jsonify(logs)


@api_bp.get("/agents/<agent_id>/events/download")
def download_agent_events(agent_id: str):
    limit = int(request.args.get("limit", 10000))
    risk_level = request.args.get("risk_level")
    event_type = request.args.get("event_type")

    events = repo().list_events(
        agent_id=agent_id,
        risk_level=risk_level,
        event_type=event_type,
        limit=limit,
    )
    return events_to_csv_response(events, filename=f"agent_{agent_id}_events.csv")


@api_bp.get("/events")
def get_events():
    limit, agent_id, risk_level, event_type = event_filters_from_request(default_limit=500)

    events = repo().list_events(
        agent_id=agent_id,
        risk_level=risk_level,
        event_type=event_type,
        limit=limit,
    )
    return jsonify(events)


@api_bp.get("/events/download")
def download_events():
    limit, agent_id, risk_level, event_type = event_filters_from_request(default_limit=10000)
    events = repo().list_events(
        agent_id=agent_id,
        risk_level=risk_level,
        event_type=event_type,
        limit=limit,
    )
    return events_to_csv_response(events, filename="fim_events.csv")


@api_bp.get("/dashboard/summary")
def dashboard_summary():
    return jsonify(repo().dashboard_summary())


@api_bp.get("/alerts")
def get_alerts():
    limit = int(request.args.get("limit", 300))
    severity = request.args.get("severity")
    agent_id = request.args.get("agent_id")
    alerts = repo().list_alerts(
        unread_only=False,
        severity=severity,
        agent_id=agent_id,
        limit=limit,
    )
    return jsonify(alerts)


@api_bp.get("/alerts/unread")
def get_unread_alerts():
    limit = int(request.args.get("limit", 300))
    severity = request.args.get("severity")
    agent_id = request.args.get("agent_id")
    alerts = repo().list_alerts(
        unread_only=True,
        severity=severity,
        agent_id=agent_id,
        limit=limit,
    )
    return jsonify(alerts)


@api_bp.get("/alerts/summary")
def get_alert_summary():
    return jsonify(repo().alerts_summary())


@api_bp.patch("/alerts/<alert_id>/read")
def mark_alert_read(alert_id: str):
    updated = repo().mark_alert_read(alert_id)
    if not updated:
        return jsonify({"error": "alert not found"}), 404

    counts = repo().alerts_summary()
    socketio_instance().emit("alert:read", updated)
    socketio_instance().emit("alerts:update", counts)
    return jsonify({"status": "alert_marked_read", "alert": updated, "counts": counts})


@api_bp.delete("/alerts")
def clear_alerts():
    severity = request.args.get("severity")
    agent_id = request.args.get("agent_id")
    deleted_count = repo().clear_alerts(severity=severity, agent_id=agent_id)
    counts = repo().alerts_summary()
    socketio_instance().emit("alerts:cleared", {"deleted": deleted_count})
    socketio_instance().emit("alerts:update", counts)
    return jsonify({"status": "alerts_cleared", "deleted": deleted_count, "counts": counts})


@api_bp.get("/deploy/agents")
def list_deploy_agents():
    return jsonify(repo().list_agent_profiles())


@api_bp.post("/deploy/agents")
def create_deploy_agent():
    payload = _deployment_payload(request.get_json(silent=True) or {})
    try:
        profile = repo().create_agent_profile(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    socketio_instance().emit("deploy:profiles:update", {"agent_id": profile["agent_id"]})
    return jsonify(profile), 201


@api_bp.get("/deploy/agents/<agent_id>")
def get_deploy_agent(agent_id: str):
    profile = repo().get_agent_profile(agent_id)
    if not profile:
        return jsonify({"error": "agent profile not found"}), 404
    return jsonify(profile)


@api_bp.put("/deploy/agents/<agent_id>")
def update_deploy_agent(agent_id: str):
    payload = _deployment_payload(request.get_json(silent=True) or {})
    try:
        updated = repo().update_agent_profile(agent_id, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not updated:
        return jsonify({"error": "agent profile not found"}), 404

    socketio_instance().emit("deploy:profiles:update", {"agent_id": agent_id})
    return jsonify(updated)


@api_bp.patch("/deploy/agents/<agent_id>/enabled")
def toggle_deploy_agent(agent_id: str):
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", True))
    updated = repo().set_agent_profile_enabled(agent_id, enabled=enabled)
    if not updated:
        return jsonify({"error": "agent profile not found"}), 404

    socketio_instance().emit("deploy:profiles:update", {"agent_id": agent_id})
    return jsonify(updated)


@api_bp.post("/deploy/agents/<agent_id>/token")
def regenerate_deploy_agent_token(agent_id: str):
    updated = repo().regenerate_agent_profile_token(agent_id)
    if not updated:
        return jsonify({"error": "agent profile not found"}), 404

    socketio_instance().emit("deploy:profiles:update", {"agent_id": agent_id})
    return jsonify(updated)


@api_bp.delete("/deploy/agents/<agent_id>")
def delete_deploy_agent(agent_id: str):
    deleted = repo().delete_agent_profile(agent_id)
    if not deleted:
        return jsonify({"error": "agent profile not found"}), 404

    socketio_instance().emit("deploy:profiles:update", {"agent_id": agent_id})
    return jsonify({"status": "deleted", "agent_id": agent_id})


@api_bp.get("/deploy/agents/<agent_id>/config")
def deploy_agent_config(agent_id: str):
    profile = repo().get_agent_profile(agent_id)
    if not profile:
        return jsonify({"error": "agent profile not found"}), 404

    payload = build_agent_config(
        profile,
        _public_server_base_url(),
        server_base_urls=_public_server_base_urls(),
    )
    return jsonify(payload)


@api_bp.get("/deploy/agents/<agent_id>/install-command")
def deploy_agent_install_command(agent_id: str):
    profile = repo().get_agent_profile(agent_id)
    if not profile:
        return jsonify({"error": "agent profile not found"}), 404

    platform_name = request.args.get("platform", "windows").strip().lower()
    try:
        command = build_install_command(profile, platform_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"command": command})


@api_bp.get("/deploy/agents/<agent_id>/package")
def deploy_agent_package(agent_id: str):
    profile = repo().get_agent_profile(agent_id)
    if not profile:
        return jsonify({"error": "agent profile not found"}), 404

    platform_name = request.args.get("platform", "windows").strip().lower()
    try:
        filename, package_bytes = build_agent_package(
            profile,
            platform_name,
            _public_server_base_url(),
            server_base_urls=_public_server_base_urls(),
        )
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({"error": str(exc)}), 400

    return Response(
        package_bytes,
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
