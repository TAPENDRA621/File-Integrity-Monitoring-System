import os

from flask import jsonify, request

from app import create_app, socketio

app = create_app()


@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok"}), 200


@app.before_request
def log_incoming_agent_registration():
    if request.path == "/api/agents/register" and request.method == "POST":
        print(f"[INFO] Incoming agent registration request from {request.remote_addr}")


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))

    print("[INFO] Starting FIMS ingestion server")
    print(f"[INFO] Bind host: {host}")
    if host == "0.0.0.0":
        print("[INFO] Server is reachable on all network interfaces")
    else:
        print("[WARN] Server is not bound to all interfaces; remote agents may not connect")
    print(f"[INFO] Ingestion URL: http://{host}:{port}")
    print(f"[INFO] Health endpoint: http://{host}:{port}/api/health")

    socketio.run(app, host=host, port=port)
