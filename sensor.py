
import os
import queue
import re
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict

import requests
from flask import Flask, jsonify, request

from utils.risk import classify_risk


UPSTREAM_BASE_URL = os.environ.get("FIM_UPSTREAM_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
SENSOR_HOST = os.environ.get("FIM_SENSOR_HOST", "0.0.0.0")
SENSOR_PORT = int(os.environ.get("FIM_SENSOR_PORT", "5100"))
FORWARD_TIMEOUT_SECONDS = int(os.environ.get("FIM_SENSOR_FORWARD_TIMEOUT", "8"))
FORWARD_RETRY_DELAY_SECONDS = float(os.environ.get("FIM_SENSOR_RETRY_DELAY", "2"))
MAX_QUEUE_SIZE = int(os.environ.get("FIM_SENSOR_MAX_QUEUE", "5000"))
SYSLOG_ENABLED = os.environ.get("FIM_SENSOR_SYSLOG_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
SYSLOG_BIND_HOST = os.environ.get("FIM_SENSOR_SYSLOG_HOST", "0.0.0.0")
SYSLOG_BIND_PORT = int(os.environ.get("FIM_SENSOR_SYSLOG_PORT", "5514"))
SYSLOG_MAX_PACKET_SIZE = int(os.environ.get("FIM_SENSOR_SYSLOG_MAX_PACKET", "8192"))
SYSLOG_SOCKET_TIMEOUT_SECONDS = float(os.environ.get("FIM_SENSOR_SYSLOG_SOCKET_TIMEOUT", "1.0"))

app = Flask(__name__)
session = requests.Session()
forward_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=MAX_QUEUE_SIZE)

received_count = 0
forwarded_count = 0
failed_count = 0
syslog_received_count = 0
syslog_enqueued_count = 0
syslog_dropped_count = 0
lock = threading.Lock()
known_agents: Dict[str, Dict[str, Any]] = {}
worker_started = False
syslog_worker_started = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_identifier(raw_value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw_value or "").strip())
    cleaned = cleaned.strip("-")
    return cleaned.lower()


def infer_event_type(message: str) -> str:
    lowered = (message or "").lower()
    if any(token in lowered for token in ("delete", "deleted", "remove", "removed", "unlink", "purge")):
        return "deleted"
    if any(token in lowered for token in ("create", "created", "new file", "added", "add ")):
        return "created"
    return "modified"


def parse_syslog_payload(raw_message: str, source_ip: str) -> Dict[str, Any]:
    message = (raw_message or "").strip().replace("\x00", "")
    if not message:
        return {}

    if message.startswith("<"):
        pri_end = message.find(">")
        if pri_end > 0:
            message = message[pri_end + 1 :].strip()

    hostname = source_ip
    body = message
    parts = message.split()

    if len(parts) >= 5:
        maybe_month = parts[0]
        if len(maybe_month) == 3 and maybe_month[0].isalpha() and ":" in parts[2]:
            hostname = parts[3]
            body = " ".join(parts[4:])

    host_id = normalize_identifier(hostname) or normalize_identifier(source_ip) or "network-device"
    device_agent_id = f"net-{host_id}"
    event_type = infer_event_type(body)

    return {
        "agent_id": device_agent_id,
        "hostname": hostname,
        "ip_address": source_ip,
        "timestamp_utc": utc_now_iso(),
        "file_path": f"network/syslog/{hostname}",
        "event_type": event_type,
        "hash_before": None,
        "hash_after": body[:1024],
        "risk_level": classify_risk(f"network/syslog/{hostname}", event_type),
    }


def enqueue_for_forward(endpoint: str, payload: Dict[str, Any]) -> bool:
    item = {
        "endpoint": endpoint,
        "payload": payload,
        "attempts": 0,
        "queued_at_utc": utc_now_iso(),
    }
    try:
        forward_queue.put_nowait(item)
        return True
    except queue.Full:
        return False


def update_known_agents(payload: Dict[str, Any]) -> None:
    agent_id = payload.get("agent_id")
    if not agent_id:
        return

    with lock:
        current = known_agents.get(agent_id, {})
        current.update(
            {
                "agent_id": agent_id,
                "hostname": payload.get("hostname", current.get("hostname", "unknown-host")),
                "ip_address": payload.get("ip_address", current.get("ip_address", "0.0.0.0")),
                "last_seen_utc": payload.get("timestamp_utc") or payload.get("last_seen_utc") or utc_now_iso(),
                "source": "sensor-relay",
            }
        )
        known_agents[agent_id] = current


def increment_counter(counter_name: str) -> None:
    global received_count, forwarded_count, failed_count
    global syslog_received_count, syslog_enqueued_count, syslog_dropped_count
    with lock:
        if counter_name == "received":
            received_count += 1
        elif counter_name == "forwarded":
            forwarded_count += 1
        elif counter_name == "failed":
            failed_count += 1
        elif counter_name == "syslog_received":
            syslog_received_count += 1
        elif counter_name == "syslog_enqueued":
            syslog_enqueued_count += 1
        elif counter_name == "syslog_dropped":
            syslog_dropped_count += 1


def forward_worker() -> None:
    while True:
        item = forward_queue.get()
        endpoint = item["endpoint"]
        payload = item["payload"]
        attempts = int(item.get("attempts", 0)) + 1

        url = f"{UPSTREAM_BASE_URL}{endpoint}"
        try:
            response = session.post(url, json=payload, timeout=FORWARD_TIMEOUT_SECONDS)
            response.raise_for_status()
            increment_counter("forwarded")
        except requests.RequestException:
            increment_counter("failed")
            item["attempts"] = attempts
            time.sleep(FORWARD_RETRY_DELAY_SECONDS)
            try:
                forward_queue.put_nowait(item)
            except queue.Full:
                pass
        finally:
            forward_queue.task_done()


def syslog_udp_worker() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((SYSLOG_BIND_HOST, SYSLOG_BIND_PORT))
        sock.settimeout(SYSLOG_SOCKET_TIMEOUT_SECONDS)

        while True:
            try:
                packet, remote = sock.recvfrom(SYSLOG_MAX_PACKET_SIZE)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(1)
                continue

            source_ip = remote[0] if remote else "0.0.0.0"
            raw_message = packet.decode("utf-8", errors="replace")
            increment_counter("syslog_received")

            payload = parse_syslog_payload(raw_message, source_ip)
            if not payload:
                increment_counter("syslog_dropped")
                continue

            update_known_agents(payload)
            accepted = enqueue_for_forward("/api/events", payload)
            if accepted:
                increment_counter("received")
                increment_counter("syslog_enqueued")
            else:
                increment_counter("syslog_dropped")
    finally:
        sock.close()


@app.get("/sensor/health")
def sensor_health():
    with lock:
        return jsonify(
            {
                "status": "ok",
                "upstream_base_url": UPSTREAM_BASE_URL,
                "queue_size": forward_queue.qsize(),
                "received_count": received_count,
                "forwarded_count": forwarded_count,
                "failed_count": failed_count,
                "known_agents": len(known_agents),
                "syslog": {
                    "enabled": SYSLOG_ENABLED,
                    "bind": f"{SYSLOG_BIND_HOST}:{SYSLOG_BIND_PORT}",
                    "received_count": syslog_received_count,
                    "enqueued_count": syslog_enqueued_count,
                    "dropped_count": syslog_dropped_count,
                },
            }
        )


@app.get("/sensor/agents")
def sensor_agents():
    with lock:
        agents = list(known_agents.values())
    return jsonify(agents)


@app.post("/api/agents/register")
def relay_register_agent():
    payload = request.get_json(silent=True) or {}
    if not payload.get("agent_id"):
        return jsonify({"error": "agent_id is required"}), 400

    update_known_agents(payload)
    increment_counter("received")

    accepted = enqueue_for_forward("/api/agents/register", payload)
    if not accepted:
        return jsonify({"error": "sensor queue full"}), 503

    return jsonify({"status": "queued", "forward_to": "/api/agents/register"}), 202


@app.post("/api/agents/heartbeat")
def relay_heartbeat():
    payload = request.get_json(silent=True) or {}
    if not payload.get("agent_id"):
        return jsonify({"error": "agent_id is required"}), 400

    update_known_agents(payload)
    increment_counter("received")

    accepted = enqueue_for_forward("/api/agents/heartbeat", payload)
    if not accepted:
        return jsonify({"error": "sensor queue full"}), 503

    return jsonify({"status": "queued", "forward_to": "/api/agents/heartbeat"}), 202


@app.post("/api/events")
def relay_event():
    payload = request.get_json(silent=True) or {}
    required_fields = ["agent_id", "hostname", "ip_address", "file_path", "event_type"]
    missing = [field for field in required_fields if not payload.get(field)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    update_known_agents(payload)
    increment_counter("received")

    accepted = enqueue_for_forward("/api/events", payload)
    if not accepted:
        return jsonify({"error": "sensor queue full"}), 503

    return jsonify({"status": "queued", "forward_to": "/api/events"}), 202


def start_workers() -> None:
    global worker_started, syslog_worker_started
    if worker_started:
        return
    worker = threading.Thread(target=forward_worker, daemon=True)
    worker.start()
    worker_started = True

    if SYSLOG_ENABLED and not syslog_worker_started:
        syslog_worker = threading.Thread(target=syslog_udp_worker, daemon=True)
        syslog_worker.start()
        syslog_worker_started = True


start_workers()


if __name__ == "__main__":
    app.run(host=SENSOR_HOST, port=SENSOR_PORT)
