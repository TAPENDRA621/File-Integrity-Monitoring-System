import json
import logging
import os
import re
import socket
import threading
from typing import List


DEFAULT_DISCOVERY_PORT = 50505


def _parse_port(value, default: int) -> int:
    try:
        parsed = int(value)
        if 1 <= parsed <= 65535:
            return parsed
    except (TypeError, ValueError):
        pass
    return default


def _normalize_base_urls(values: List[str]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for value in values:
        url = str(value or "").strip().rstrip("/")
        if not url:
            continue
        lowered = url.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(url)
    return ordered


def _configured_server_base_urls(server_port: int, scheme: str) -> List[str]:
    urls: List[str] = []

    primary = str(os.environ.get("FIMS_AGENT_SERVER_BASE_URL", "")).strip().rstrip("/")
    if primary:
        urls.append(primary)

    raw_values = str(os.environ.get("FIMS_AGENT_SERVER_BASE_URLS", ""))
    if raw_values:
        for token in re.split(r"[\r\n,;]+", raw_values):
            cleaned = token.strip().rstrip("/")
            if cleaned:
                urls.append(cleaned)

    hostname = str(os.environ.get("FIMS_DISCOVERY_HOSTNAME", "")).strip() or socket.gethostname()
    if hostname:
        urls.append(f"{scheme}://{hostname}:{server_port}")

    return _normalize_base_urls(urls)


class UdpDiscoveryResponder:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("fim.discovery")
        flag = str(os.environ.get("FIMS_DISCOVERY_ENABLED", "1")).strip().lower()
        self.enabled = flag not in {"0", "false", "no", "off"}
        self.discovery_port = _parse_port(os.environ.get("FIMS_DISCOVERY_PORT"), DEFAULT_DISCOVERY_PORT)
        self.server_port = _parse_port(os.environ.get("PORT", "5000"), 5000)
        scheme = str(os.environ.get("FIMS_DISCOVERY_SCHEME", "http")).strip().lower()
        self.scheme = "https" if scheme == "https" else "http"
        self._thread = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self.enabled:
            self.logger.info("UDP discovery responder is disabled via FIMS_DISCOVERY_ENABLED")
            return

        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._serve,
            name="fim-udp-discovery",
            daemon=True,
        )
        self._thread.start()
        self.logger.info("UDP discovery responder listening on port %s", self.discovery_port)

    def _is_discovery_request(self, payload: bytes) -> bool:
        text = payload.decode("utf-8", errors="ignore").strip()
        if text == "FIMS_DISCOVERY_REQUEST_V1":
            return True

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return False

        if not isinstance(parsed, dict):
            return False

        request_type = str(parsed.get("type") or "").strip().upper()
        return request_type == "FIMS_DISCOVERY_REQUEST"

    def _response_payload(self) -> bytes:
        payload = {
            "type": "FIMS_DISCOVERY_RESPONSE",
            "version": 1,
            "server_port": self.server_port,
            "server_base_urls": _configured_server_base_urls(self.server_port, self.scheme),
        }
        return json.dumps(payload).encode("utf-8")

    def _serve(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass

        try:
            sock.bind(("0.0.0.0", self.discovery_port))
        except OSError as exc:
            self.logger.warning("Unable to start UDP discovery responder on port %s: %s", self.discovery_port, exc)
            sock.close()
            return

        with sock:
            sock.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    payload, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if not self._is_discovery_request(payload):
                    continue

                try:
                    sock.sendto(self._response_payload(), addr)
                except OSError:
                    continue
