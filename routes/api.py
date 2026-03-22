import csv
import io
import json
import os
import re
from datetime import timedelta

from flask import Blueprint, Response, current_app, jsonify, request

from utils.time_utils import parse_utc_timestamp

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
    agent_id = payload.get("agent_id") or _normalize_agent_id(payload.get("agent_name", ""))
    if not agent_id:
        return jsonify({"error": "agent_id is required"}), 400

    normalized_payload = dict(payload)
    normalized_payload["agent_id"] = agent_id

    if payload.get("agent_name") and not payload.get("hostname"):
        normalized_payload["hostname"] = payload["agent_name"]

    if payload.get("agent_ip_address") and not payload.get("ip_address"):
        normalized_payload["ip_address"] = payload["agent_ip_address"]

    saved = repo().register_agent(normalized_payload)

    metadata = _load_agent_store()
    existing_meta = metadata.get(agent_id, {})
    metadata[agent_id] = {
        "agent_name": payload.get("agent_name") or existing_meta.get("agent_name") or saved.get("hostname"),
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

    updated = repo().update_heartbeat(agent_id, payload.get("timestamp_utc"))
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
    summary = repo().dashboard_summary()

    socketio_instance().emit("event:new", event)
    socketio_instance().emit("dashboard:update", summary)

    return jsonify({"status": "event_stored", "event": event}), 201


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
