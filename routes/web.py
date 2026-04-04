from flask import Blueprint, render_template

web_bp = Blueprint("web", __name__)


@web_bp.get("/")
def dashboard_page():
    return render_template("dashboard.html")


@web_bp.get("/agents")
def agents_page():
    return render_template("agents.html")


@web_bp.get("/agents/<agent_id>/logs")
def agent_logs_page(agent_id: str):
    return render_template("agent_logs.html", agent_id=agent_id)


@web_bp.get("/events")
def events_page():
    return render_template("events.html")


@web_bp.get("/deploy")
def deploy_page():
    return render_template("deploy.html")
