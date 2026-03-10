"""
WSGI entry point for Gunicorn.

Usage (example):
    gunicorn -k eventlet -w 1 --bind 0.0.0.0:${PORT:-5000} wsgi:app
"""

from server import app  # Flask application with Flask-SocketIO initialized

# Gunicorn will import `app` from this module.
# For local debugging you can still do:
if __name__ == "__main__":
    from server import socketio

    socketio.run(app, host="0.0.0.0", port=5000)

