from __future__ import annotations

import re
from typing import Callable, Dict, List


# Rule-based chatbot focused only on this FIM system domain.
INTENTS: List[Dict[str, object]] = [
    {
        "id": "system_overview",
        "keywords": [
            "what is this system",
            "what does this dashboard do",
            "dashboard purpose",
            "what is fim",
            "file integrity monitoring",
        ],
        "answer": "This dashboard is for File Integrity Monitoring (FIM). It tracks file changes from connected agents, shows events and alerts, and helps you manage deployment and monitoring health.",
    },
    {
        "id": "agent_definition",
        "keywords": [
            "what is agent",
            "agent meaning",
            "agent role",
            "server and agent",
            "difference between server and agent",
        ],
        "answer": "An agent is the endpoint monitor running on a machine. It watches configured files/folders and sends registration, heartbeat, and file-change events to the central server.",
    },
    {
        "id": "agent_id",
        "keywords": [
            "what is agent id",
            "agent id",
            "why agent id",
            "use of agent id",
        ],
        "answer": "Agent ID is the unique identity for each endpoint in the system. The server uses it to map heartbeats, events, logs, and deployment profile data to the correct machine.",
    },
    {
        "id": "agent_name",
        "keywords": [
            "what is agent name",
            "agent name",
            "why agent name",
            "use of agent name",
        ],
        "answer": "Agent name is a human-friendly label shown in the dashboard. It helps operators recognize systems more easily than IDs alone.",
    },
    {
        "id": "port_field",
        "keywords": [
            "what is port field",
            "port used for",
            "agent port",
            "why port",
        ],
        "answer": "The port field is metadata for endpoint/network context in dashboard workflows. Core monitoring traffic uses the configured server API endpoints.",
    },
    {
        "id": "add_agent",
        "keywords": [
            "how do i add agent",
            "how to add agent",
            "connect an agent",
            "register agent",
            "onboard agent",
        ],
        "answer": "Use the Deploy Agent flow to create a profile and generate an install package, then run the installer on the endpoint. The agent registers with the server and appears in the Agents table.",
    },
    {
        "id": "file_monitoring",
        "keywords": [
            "how does file monitoring work",
            "how monitoring works",
            "watch files",
            "monitor multiple paths",
        ],
        "answer": "The agent monitors configured paths with filesystem watchers plus periodic scan logic. It reports created/modified/deleted events with hashes so integrity changes are visible in the dashboard.",
    },
    {
        "id": "event_types",
        "keywords": [
            "created modified deleted",
            "what does created mean",
            "what does modified mean",
            "what does deleted mean",
            "event type",
        ],
        "answer": "Created means a new file appeared, Modified means a file content hash changed, and Deleted means a previously tracked file was removed.",
    },
    {
        "id": "heartbeat",
        "keywords": [
            "what is heartbeat",
            "why heartbeat",
            "heartbeat important",
            "agent status active inactive",
        ],
        "answer": "Heartbeat is the agent's periodic status signal to the server. It updates last-seen time and helps determine whether an agent is Active or Inactive.",
    },
    {
        "id": "communication",
        "keywords": [
            "server communicate with agents",
            "agent communicate with server",
            "api communication",
            "how data sent",
        ],
        "answer": "Agents communicate with the server through API endpoints for registration, heartbeat, and events. The dashboard then reads aggregated data from server APIs and receives live updates.",
    },
    {
        "id": "sensor_syslog",
        "keywords": [
            "what is sensor",
            "what is syslog",
            "syslog used for",
            "sensor flow",
            "relay",
        ],
        "answer": "Sensor/syslog mode lets logs from distributed networks or devices be relayed into the same central FIM server, so monitoring remains unified in one dashboard.",
    },
    {
        "id": "deploy_agents",
        "keywords": [
            "how do i deploy agents",
            "deploy agent",
            "installation package",
            "windows linux install",
        ],
        "answer": "Go to Deploy Agent, create/update a profile, download the platform package, and run the installer on endpoints. The service/task starts automatically and reports back to the server.",
    },
    {
        "id": "use_dashboard",
        "keywords": [
            "how do i use dashboard",
            "how to use dashboard",
            "dashboard usage",
            "where to see events",
            "where to see agents",
        ],
        "answer": "Use Dashboard for live summary and alerts, Agents for endpoint status/log access, Events for filtered history/export, and Deploy Agent for onboarding and package generation.",
    },
    {
        "id": "dynamic_totals",
        "keywords": [
            "how many agents",
            "total agents",
            "total events",
            "high risk events",
            "current stats",
        ],
        "answer_builder": "build_dynamic_totals_answer",
    },
]

DEFAULT_SCOPE_MESSAGE = (
    "I can help with this File Integrity Monitoring dashboard only. "
    "Please ask about agents, events, alerts, deployment, heartbeat, monitoring paths, sensor/syslog, or dashboard usage."
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _score_intent(question: str, keywords: List[str]) -> int:
    score = 0
    for keyword in keywords:
        token = _normalize(keyword)
        if token and token in question:
            score += max(1, len(token.split()))
    return score


def _build_dynamic_totals_answer(context: Dict[str, object]) -> str:
    cards = context.get("cards") if isinstance(context, dict) else None
    if not isinstance(cards, dict):
        return "Live totals are available on the dashboard cards at the top of the page."

    total_agents = cards.get("total_agents", 0)
    active_agents = cards.get("active_agents", 0)
    total_events_today = cards.get("total_events_today", 0)
    high_risk_events = cards.get("high_risk_events", 0)
    return (
        f"Current summary: total agents {total_agents}, active agents {active_agents}, "
        f"events today {total_events_today}, high-risk events {high_risk_events}."
    )


def get_chatbot_response(question: str, context_provider: Callable[[], Dict[str, object]] | None = None) -> str:
    normalized_question = _normalize(question)
    if not normalized_question:
        return "Please type a system-related question, for example: 'What is an agent ID?'"

    best_intent: Dict[str, object] | None = None
    best_score = 0

    for intent in INTENTS:
        keywords = intent.get("keywords")
        if not isinstance(keywords, list):
            continue
        score = _score_intent(normalized_question, keywords)
        if score > best_score:
            best_intent = intent
            best_score = score

    if not best_intent or best_score <= 0:
        return DEFAULT_SCOPE_MESSAGE

    answer = best_intent.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer

    builder_name = best_intent.get("answer_builder")
    if builder_name == "build_dynamic_totals_answer":
        context = context_provider() if callable(context_provider) else {}
        return _build_dynamic_totals_answer(context if isinstance(context, dict) else {})

    return DEFAULT_SCOPE_MESSAGE
