import os
import logging

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from models import FIMRepository
from routes import api_bp, web_bp
from utils.discovery import UdpDiscoveryResponder

socketio = SocketIO()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "fim-dev-secret")

    CORS(app, resources={r"/api/*": {"origins": "*"}})

    requested_async_mode = os.environ.get("SOCKETIO_ASYNC_MODE", "threading")
    if requested_async_mode == "eventlet":
        try:
            import eventlet  # noqa: F401
        except Exception:
            requested_async_mode = "threading"

    socketio.init_app(
        app,
        cors_allowed_origins="*",
        async_mode=requested_async_mode,
        manage_session=False,
    )
    app.extensions["socketio"] = socketio

    app.config["repo"] = FIMRepository(os.environ.get("FIMS_DB_PATH"))
    app.config["agent_store_path"] = os.environ.get(
        "FIMS_AGENT_STORE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "agents_registry.json"),
    )

    discovery_responder = UdpDiscoveryResponder(app.logger)
    discovery_responder.start()
    app.extensions["discovery_responder"] = discovery_responder

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

    @socketio.on("connect")
    def socket_connect():
        app.logger.info("Dashboard client connected")

    @socketio.on("disconnect")
    def socket_disconnect():
        app.logger.info("Dashboard client disconnected")

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    flask_app = create_app()
    port = int(os.environ.get("PORT", "5000"))
    socketio.run(flask_app, host="0.0.0.0", port=port)
