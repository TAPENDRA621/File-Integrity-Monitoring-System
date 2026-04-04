import argparse
import hashlib
import json
import logging
import os
import platform
import re
import socket
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import timezone, datetime
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Tuple

import requests
from colorama import Fore, Style, init
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from utils.windows_context import (
    detect_runtime_context,
    validate_monitor_paths,
)

def _runtime_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _default_state_root() -> str:
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return os.path.join(program_data, "FIMAgent")
    if sys.platform.startswith("linux"):
        return "/var/lib/fim-agent"
    return _runtime_base_dir()


def _default_log_root() -> str:
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return os.path.join(program_data, "FIMAgent")
    if sys.platform.startswith("linux"):
        return "/var/log/fim-agent"
    return _runtime_base_dir()


def _resolve_writable_dir(preferred: str, fallback: str) -> str:
    for candidate in [preferred, fallback]:
        try:
            os.makedirs(candidate, exist_ok=True)
            return candidate
        except OSError:
            continue
    return fallback


def _state_dir() -> str:
    configured = os.environ.get("FIM_AGENT_STATE_DIR", "").strip()
    if configured:
        return configured
    return _resolve_writable_dir(
        os.path.join(_default_state_root(), "state"),
        os.path.join(_runtime_base_dir(), "state"),
    )


def _log_dir() -> str:
    configured = os.environ.get("FIM_AGENT_LOG_DIR", "").strip()
    if configured:
        return configured
    return _resolve_writable_dir(
        os.path.join(_default_log_root(), "logs"),
        os.path.join(_runtime_base_dir(), "logs"),
    )


def _ensure_dir(path: str) -> None:
    if not path:
        return
    os.makedirs(path, exist_ok=True)


CONFIG_FILE = os.path.join(_runtime_base_dir(), "config.json")
BUFFER_FILE = os.path.join(_state_dir(), "event_buffer.jsonl")
HASH_SNAPSHOT_FILE = os.path.join(_state_dir(), "hash_snapshot.json")
AGENT_LOG_FILE = os.path.join(_log_dir(), "agent-diagnostics.log")
MAX_BUFFER_EVENTS = 5000
MODIFIED_SUPPRESS_WINDOW_SECONDS = 1.5

LOGGER = logging.getLogger("fim_agent")

init(autoreset=True)


def setup_runtime_logging() -> None:
    if LOGGER.handlers:
        return

    LOGGER.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    try:
        _ensure_dir(_log_dir())
        file_handler = RotatingFileHandler(
            AGENT_LOG_FILE,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    except OSError:
        pass

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)


def install_unhandled_exception_logging() -> None:
    def _hook(exc_type, exc_value, exc_traceback):
        LOGGER.error("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = _hook

    def _thread_hook(args):
        LOGGER.error(
            "Unhandled thread exception in %s",
            getattr(args.thread, "name", "unknown-thread"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook


def _clean_path_token(path: str) -> str:
    token = str(path or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1].strip()
    return os.path.expandvars(os.path.expanduser(token))


def _normalized_path_key(path: str) -> str:
    cleaned = _clean_path_token(path)
    return os.path.normcase(os.path.normpath(os.path.abspath(cleaned)))


def _deduplicate_paths(paths: List[str]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for path in paths:
        cleaned = _clean_path_token(path)
        if not cleaned:
            continue
        normalized = os.path.abspath(cleaned)
        key = _normalized_path_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _normalize_server_base_urls(values: List[str]) -> List[str]:
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


def load_local_config() -> Optional[Dict]:
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        LOGGER.info("Startup step: config file loaded from %s", CONFIG_FILE)
        with open(CONFIG_FILE, "r", encoding="utf-8") as file_handle:
            loaded = json.load(file_handle)
            return loaded if isinstance(loaded, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_local_config(config_data: Dict) -> bool:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as file_handle:
            json.dump(config_data, file_handle, indent=2)
        return True
    except OSError as exc:
        LOGGER.warning("Unable to save local config to %s: %s", CONFIG_FILE, exc)
        return False


def reset_local_config() -> bool:
    if not os.path.exists(CONFIG_FILE):
        return False
    os.remove(CONFIG_FILE)
    return True


def _parse_positive_int(value, default_value: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default_value
    except (TypeError, ValueError):
        return default_value


def _derive_paths_from_targets(monitor_targets: List[str]) -> Dict[str, List[str]]:
    scan_paths: List[str] = []
    monitored_files: List[str] = []

    for target in monitor_targets:
        absolute_target = os.path.abspath(target)
        if os.path.isfile(absolute_target):
            monitored_files.append(absolute_target)
            scan_paths.append(os.path.dirname(absolute_target) or os.getcwd())
        else:
            scan_paths.append(absolute_target)

    return {
        "monitor_targets": _deduplicate_paths(monitor_targets),
        "scan_paths": _deduplicate_paths(scan_paths),
        "monitored_files": _deduplicate_paths(monitored_files),
    }


def _normalize_config_data(config_data: Dict) -> Dict:
    server_base_url = str(config_data.get("server_base_url", "")).strip().rstrip("/")
    raw_server_base_urls = config_data.get("server_base_urls")

    server_base_urls_tokens: List[str] = []
    if isinstance(raw_server_base_urls, list):
        server_base_urls_tokens = [str(item).strip() for item in raw_server_base_urls]
    elif isinstance(raw_server_base_urls, str):
        server_base_urls_tokens = [item.strip() for item in re.split(r"[\r\n,;]+", raw_server_base_urls)]

    combined_server_base_urls = []
    if server_base_url:
        combined_server_base_urls.append(server_base_url)
    combined_server_base_urls.extend(server_base_urls_tokens)
    server_base_urls = _normalize_server_base_urls(combined_server_base_urls)
    if not server_base_urls and server_base_url:
        server_base_urls = _normalize_server_base_urls([server_base_url])
    if server_base_urls:
        server_base_url = server_base_urls[0]

    monitor_targets = config_data.get("monitor_targets")
    if isinstance(monitor_targets, list):
        monitor_targets = _deduplicate_paths([str(path) for path in monitor_targets if str(path).strip()])
    else:
        monitor_targets = []

    scan_paths = config_data.get("scan_paths")
    if isinstance(scan_paths, list):
        scan_paths = _deduplicate_paths([str(path) for path in scan_paths if str(path).strip()])
    else:
        scan_paths = []

    monitored_files = config_data.get("monitored_files")
    if isinstance(monitored_files, list):
        monitored_files = _deduplicate_paths([str(path) for path in monitored_files if str(path).strip()])
    else:
        monitored_files = []

    if not scan_paths and monitor_targets:
        inferred = _derive_paths_from_targets(monitor_targets)
        scan_paths = inferred["scan_paths"]
        if not monitored_files:
            monitored_files = inferred["monitored_files"]

    if not monitor_targets and scan_paths:
        monitor_targets = _deduplicate_paths(list(scan_paths))

    default_agent_id = socket.gethostname()
    agent_id = str(config_data.get("agent_id") or default_agent_id).strip() or default_agent_id
    agent_name = str(config_data.get("agent_name") or agent_id).strip() or agent_id

    return {
        "server_base_url": server_base_url,
        "server_base_urls": server_base_urls,
        "agent_name": agent_name,
        "agent_id": agent_id,
        "agent_port": config_data.get("agent_port"),
        "heartbeat_seconds": _parse_positive_int(config_data.get("heartbeat_seconds", 30), 30),
        "poll_seconds": _parse_positive_int(config_data.get("poll_seconds", 15), 15),
        "monitor_mode": config_data.get("monitor_mode", "multiple_paths"),
        "monitor_targets": monitor_targets,
        "scan_paths": scan_paths,
        "monitored_files": monitored_files,
        "enrollment_token": config_data.get("enrollment_token"),
    }


def _is_valid_config_data(config_data: Optional[Dict]) -> bool:
    if not isinstance(config_data, dict):
        return False

    normalized = _normalize_config_data(config_data)
    has_valid_url = bool(normalized["server_base_urls"])
    has_paths = bool(normalized["scan_paths"])
    return has_valid_url and has_paths


def _agent_config_from_normalized(normalized: Dict) -> "AgentConfig":
    return AgentConfig(
        server_base_url=normalized["server_base_url"],
        server_base_urls=normalized["server_base_urls"],
        agent_name=normalized["agent_name"],
        agent_id=normalized["agent_id"],
        agent_port=normalized["agent_port"],
        heartbeat_seconds=normalized["heartbeat_seconds"],
        poll_seconds=normalized["poll_seconds"],
        monitor_mode=normalized["monitor_mode"],
        monitor_targets=normalized["monitor_targets"],
        scan_paths=normalized["scan_paths"],
        monitored_files=normalized["monitored_files"],
        enrollment_token=normalized["enrollment_token"],
    )


def build_agent_config() -> "AgentConfig":
    # Silent startup flow for packaged/service deployments:
    # 1) Load dashboard-generated config.json next to the agent binary/script
    # 2) Validate and normalize that configuration
    # 3) Exit cleanly when missing/invalid (never open interactive prompts)
    existing = load_local_config()

    if existing is None:
        raise RuntimeError(f"Missing configuration file: {CONFIG_FILE}. Deploy the dashboard-generated package that includes config.json.")

    LOGGER.info("Startup step: config parsed")
    if not _is_valid_config_data(existing):
        raise RuntimeError(f"Invalid configuration file: {CONFIG_FILE}. Ensure server_base_url and monitor paths are present.")

    normalized = _normalize_config_data(existing)
    LOGGER.info("Startup step: server URL resolved to %s", normalized["server_base_url"])
    LOGGER.info(
        "Startup step: enrollment token %s",
        "present" if normalized.get("enrollment_token") else "not present",
    )
    LOGGER.info("Startup step: monitor paths detected (%d)", len(normalized.get("scan_paths") or []))
    return _agent_config_from_normalized(normalized)


def persistable_config_snapshot(config: "AgentConfig") -> Dict:
    return {
        "server_base_url": config.server_base_url,
        "server_base_urls": config.server_base_urls,
        "agent_name": config.agent_name,
        "agent_id": config.agent_id,
        "agent_port": config.agent_port,
        "heartbeat_seconds": config.heartbeat_seconds,
        "poll_seconds": config.poll_seconds,
        "monitor_mode": config.monitor_mode,
        "monitor_targets": config.monitor_targets,
        "scan_paths": config.scan_paths,
        "monitored_files": config.monitored_files,
        "enrollment_token": config.enrollment_token,
    }


def effective_config_snapshot(config: "AgentConfig") -> Dict:
    return {
        "server_base_url": config.server_base_url,
        "server_base_urls": config.server_base_urls,
        "agent_name": config.agent_name,
        "agent_id": config.agent_id,
        "agent_port": config.agent_port,
        "heartbeat_seconds": config.heartbeat_seconds,
        "poll_seconds": config.poll_seconds,
        "monitor_mode": config.monitor_mode,
        "monitor_targets": config.monitor_targets,
        "scan_paths": config.scan_paths,
        "monitored_files": config.monitored_files,
        "enrollment_token": "set" if config.enrollment_token else "",
        "config_file": CONFIG_FILE,
    }


@dataclass
class AgentConfig:
    server_base_url: str = ""
    server_base_urls: List[str] = None
    agent_name: str = ""
    agent_id: str = ""
    agent_port: Optional[int] = None
    heartbeat_seconds: int = 30
    poll_seconds: int = 15
    monitor_mode: str = "multiple_paths"
    monitor_targets: List[str] = None
    scan_paths: List[str] = None
    monitored_files: List[str] = None
    enrollment_token: Optional[str] = None

    def __post_init__(self):
        self.server_base_urls = _normalize_server_base_urls(
            [self.server_base_url] + list(self.server_base_urls or [])
        )
        if self.server_base_urls:
            self.server_base_url = self.server_base_urls[0]

        if self.scan_paths is None:
            self.scan_paths = []
        else:
            self.scan_paths = _deduplicate_paths(self.scan_paths)

        if self.monitored_files is None:
            self.monitored_files = []
        else:
            self.monitored_files = _deduplicate_paths(self.monitored_files)

        if self.monitor_targets is None:
            self.monitor_targets = _deduplicate_paths(list(self.scan_paths))
        else:
            self.monitor_targets = _deduplicate_paths(self.monitor_targets)

        default_agent_id = socket.gethostname()
        self.agent_id = (self.agent_id or "").strip() or default_agent_id
        self.agent_name = (self.agent_name or "").strip() or self.agent_id
        self.heartbeat_seconds = _parse_positive_int(self.heartbeat_seconds, 30)
        self.poll_seconds = _parse_positive_int(self.poll_seconds, 15)

        if isinstance(self.agent_port, str):
            if self.agent_port.isdigit() and 1 <= int(self.agent_port) <= 65535:
                self.agent_port = int(self.agent_port)
            else:
                self.agent_port = None

        if self.monitor_mode == "single_file" and len(self.monitored_files) != 1:
            self.monitor_mode = "multiple_paths"
        if self.monitor_mode == "single_directory" and len(self.scan_paths) != 1:
            self.monitor_mode = "multiple_paths"


class AgentEventHandler(FileSystemEventHandler):
    def __init__(self, fim_agent: "FIMAgent"):
        self.fim_agent = fim_agent

    def on_created(self, event):
        if event.is_directory:
            return
        self.fim_agent.handle_file_event("created", event.src_path)

    def on_modified(self, event):
        if event.is_directory:
            return
        self.fim_agent.handle_file_event("modified", event.src_path)

    def on_deleted(self, event):
        if event.is_directory:
            return
        self.fim_agent.handle_file_event("deleted", event.src_path)


class FIMAgent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.runtime_context = detect_runtime_context()
        self.hostname = socket.gethostname()
        self.ip_address = self._get_local_ip()
        self.file_hashes: Dict[str, Optional[str]] = {}
        self.hashes_lock = threading.Lock()
        self.session = requests.Session()
        self.observer = Observer()
        self._running = True
        self._monitored_files = {_normalized_path_key(path) for path in (self.config.monitored_files or [])}
        self._registered = False
        self._buffer_mode = False
        self._buffer_lock = threading.Lock()
        self._active_scan_paths: List[str] = []
        self._watchers_started = False
        self.monitor_status = "ok"
        self.monitor_message = ""
        self._server_base_urls = _normalize_server_base_urls(
            list(self.config.server_base_urls or [self.config.server_base_url])
        )
        self._active_server_base_url = self._server_base_urls[0] if self._server_base_urls else ""
        self._recent_file_events: Dict[str, Tuple[str, float]] = {}
        self._recent_file_events_lock = threading.Lock()

    @staticmethod
    def _normalize_path_for_compare(path: str) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _scan_paths_signature(self, paths: List[str]) -> List[str]:
        normalized = [self._normalize_path_for_compare(path) for path in (paths or []) if path]
        return sorted(set(normalized))

    def _load_hash_snapshot(self) -> Dict[str, Dict[str, Optional[str]]]:
        _ensure_dir(_state_dir())
        if not os.path.exists(HASH_SNAPSHOT_FILE):
            return {"scan_paths": [], "file_hashes": {}}

        try:
            with open(HASH_SNAPSHOT_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"scan_paths": [], "file_hashes": {}}

        # Backward compatibility in case an older plain dict is present.
        if isinstance(data, dict) and "file_hashes" not in data:
            hashes = {str(k): v for k, v in data.items() if isinstance(k, str)}
            return {"scan_paths": [], "file_hashes": hashes}

        if not isinstance(data, dict):
            return {"scan_paths": [], "file_hashes": {}}

        raw_scan_paths = data.get("scan_paths")
        raw_hashes = data.get("file_hashes")
        scan_paths = [str(path) for path in raw_scan_paths] if isinstance(raw_scan_paths, list) else []
        file_hashes = raw_hashes if isinstance(raw_hashes, dict) else {}
        sanitized_hashes = {str(path): value for path, value in file_hashes.items() if isinstance(path, str)}
        return {"scan_paths": scan_paths, "file_hashes": sanitized_hashes}

    def _save_hash_snapshot(self, file_hashes: Dict[str, Optional[str]]) -> None:
        _ensure_dir(_state_dir())
        payload = {
            "saved_at_utc": self.utc_now_iso(),
            "scan_paths": list(self._active_scan_paths or self.config.scan_paths or []),
            "file_hashes": file_hashes,
        }
        try:
            with open(HASH_SNAPSHOT_FILE, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except OSError as exc:
            LOGGER.warning("Unable to persist hash snapshot to %s: %s", HASH_SNAPSHOT_FILE, exc)

    def _replay_downtime_changes(
        self,
        previous_hashes: Dict[str, Optional[str]],
        current_hashes: Dict[str, Optional[str]],
    ) -> int:
        if not previous_hashes:
            return 0

        replayed = 0
        previous_paths = set(previous_hashes)
        current_paths = set(current_hashes)

        for file_path in current_paths - previous_paths:
            self.send_event("created", file_path, None, current_hashes[file_path], replayed_offline=True)
            replayed += 1

        for file_path in previous_paths - current_paths:
            self.send_event("deleted", file_path, previous_hashes[file_path], None, replayed_offline=True)
            replayed += 1

        for file_path in current_paths & previous_paths:
            old_hash = previous_hashes[file_path]
            new_hash = current_hashes[file_path]
            if old_hash != new_hash:
                self.send_event("modified", file_path, old_hash, new_hash, replayed_offline=True)
                replayed += 1

        return replayed

    def _set_monitor_status(self, status: str, message: str = "") -> None:
        normalized_status = (status or "ok").strip().lower() or "ok"
        normalized_message = str(message or "").strip()
        changed = (self.monitor_status != normalized_status) or (self.monitor_message != normalized_message)
        self.monitor_status = normalized_status
        self.monitor_message = normalized_message
        if changed:
            LOGGER.warning("Monitor status changed to %s: %s", self.monitor_status, self.monitor_message)

    def _server_candidates(self) -> List[str]:
        ordered: List[str] = []
        seen = set()
        for candidate in [self._active_server_base_url] + self._server_base_urls:
            cleaned = str(candidate or "").strip().rstrip("/")
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(cleaned)
        return ordered

    def _set_active_server_url(self, base_url: str) -> None:
        cleaned = str(base_url or "").strip().rstrip("/")
        if not cleaned:
            return
        if self._active_server_base_url.lower() == cleaned.lower():
            return

        self._active_server_base_url = cleaned
        self.config.server_base_url = cleaned
        self._server_base_urls = _normalize_server_base_urls([cleaned] + self._server_base_urls)
        self.config.server_base_urls = list(self._server_base_urls)

        try:
            save_local_config(persistable_config_snapshot(self.config))
        except OSError:
            pass
        print(f"[INFO] Switched active server endpoint to: {cleaned}")

    def _request_with_failover(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict] = None,
        expect_json: bool = False,
        warn: bool = True,
    ):
        candidates = self._server_candidates()
        if not candidates:
            if warn:
                print("[WARN] No server endpoints configured.")
            return None

        last_exc: Optional[Exception] = None
        for base_url in candidates:
            url = f"{base_url}{endpoint}"
            try:
                if method.upper() == "GET":
                    response = self.session.get(url, timeout=8)
                else:
                    response = self.session.post(url, json=payload, timeout=8)
                response.raise_for_status()

                self._set_active_server_url(base_url)
                if self._buffer_mode:
                    self._set_buffer_mode(False, f"Connected to server endpoint {base_url}.")

                if expect_json:
                    parsed = response.json()
                    return parsed if isinstance(parsed, dict) else {}
                return True
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc

        self._set_buffer_mode(True)
        if warn and last_exc is not None:
            print(f"[WARN] Failed request {endpoint} on all configured servers: {last_exc}")
        return None

    @staticmethod
    def _play_buffer_alert_sound() -> None:
        try:
            if platform.system().lower().startswith("win"):
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                print("\a", end="", flush=True)
        except Exception:
            try:
                print("\a", end="", flush=True)
            except Exception:
                pass

    def _set_buffer_mode(self, enabled: bool, reason: str = "") -> None:
        if enabled:
            if self._buffer_mode:
                return
            self._buffer_mode = True
            message = reason or "Server unreachable. Events are being buffered locally."
            print(f"[ALERT] {message}")
            self._play_buffer_alert_sound()
            return

        if not self._buffer_mode:
            return

        self._buffer_mode = False
        message = reason or "Server connection restored. Buffered events are now synchronized."
        print(f"[ALERT] {message}")
        self._play_buffer_alert_sound()

    def _load_buffered_events_unlocked(self) -> List[Dict]:
        _ensure_dir(_state_dir())
        if not os.path.exists(BUFFER_FILE):
            return []

        events: List[Dict] = []
        try:
            with open(BUFFER_FILE, "r", encoding="utf-8") as handle:
                for line in handle:
                    raw_line = line.strip()
                    if not raw_line:
                        continue
                    try:
                        payload = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        events.append(payload)
        except OSError:
            return []
        return events

    def _save_buffered_events_unlocked(self, events: List[Dict]) -> None:
        _ensure_dir(_state_dir())
        if not events:
            try:
                if os.path.exists(BUFFER_FILE):
                    os.remove(BUFFER_FILE)
            except OSError:
                pass
            return

        try:
            with open(BUFFER_FILE, "w", encoding="utf-8") as handle:
                for event_payload in events:
                    handle.write(json.dumps(event_payload, separators=(",", ":")))
                    handle.write("\n")
        except OSError as exc:
            LOGGER.warning("Unable to persist buffered events to %s: %s", BUFFER_FILE, exc)

    def _buffered_event_count(self) -> int:
        with self._buffer_lock:
            return len(self._load_buffered_events_unlocked())

    def _append_buffered_event(self, payload: Dict) -> int:
        with self._buffer_lock:
            queued = self._load_buffered_events_unlocked()
            queued.append(payload)
            if len(queued) > MAX_BUFFER_EVENTS:
                queued = queued[-MAX_BUFFER_EVENTS:]
            self._save_buffered_events_unlocked(queued)
            return len(queued)

    def flush_buffered_events(self) -> int:
        sent_count = 0
        remaining_count = 0

        with self._buffer_lock:
            queued = self._load_buffered_events_unlocked()
            if not queued:
                return 0

            for payload in queued:
                if self._post("/api/events", payload, warn=False):
                    sent_count += 1
                    continue
                break

            remaining = queued[sent_count:]
            remaining_count = len(remaining)
            self._save_buffered_events_unlocked(remaining)

        if sent_count:
            print(f"[INFO] Flushed {sent_count} buffered event(s).")
            if remaining_count == 0:
                self._set_buffer_mode(False, "Server connection restored. Buffered events synchronized.")

        return sent_count

    @staticmethod
    def _get_local_ip() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            sock.close()

    @staticmethod
    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def sha256_file(path: str) -> Optional[str]:
        if not os.path.exists(path) or not os.path.isfile(path):
            return None
        hasher = hashlib.sha256()
        try:
            with open(path, "rb") as file_handle:
                for chunk in iter(lambda: file_handle.read(4096), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except (PermissionError, OSError):
            return None

    def _post(self, endpoint: str, payload: Dict, warn: bool = True) -> bool:
        result = self._request_with_failover("POST", endpoint, payload=payload, expect_json=False, warn=warn)
        return bool(result)

    def _post_json(self, endpoint: str, payload: Dict, warn: bool = True) -> Optional[Dict]:
        result = self._request_with_failover("POST", endpoint, payload=payload, expect_json=True, warn=warn)
        return result if isinstance(result, dict) else None

    def check_server_connectivity(self, verbose: bool = True) -> bool:
        result = self._request_with_failover("GET", "/api/agents", expect_json=False, warn=False)
        if result:
            if verbose:
                print(f"[INFO] Connected to server: {self._active_server_base_url}")
            return True

        if verbose:
            configured = ", ".join(self._server_candidates()) or "(none)"
            print(f"[ERROR] Cannot connect to configured server endpoints: {configured}")
            print("[HINT] Ensure server.py is running and listening on the expected host/port.")
            print("[HINT] Allow inbound TCP 5000 on the server firewall/router.")
        return False

    @staticmethod
    def _colored_event_line(event_type: str, file_path: str) -> str:
        normalized_event = (event_type or "").lower()
        color = Style.RESET_ALL
        if normalized_event == "created":
            color = Fore.GREEN
        elif normalized_event == "modified":
            color = Fore.YELLOW
        elif normalized_event == "deleted":
            color = Fore.RED

        return f"{color}[{normalized_event.upper()}] {file_path}{Style.RESET_ALL}"

    def _persist_server_identity(self, registration_response: Dict) -> None:
        agent_data = registration_response.get("agent") if isinstance(registration_response, dict) else None
        if not isinstance(agent_data, dict):
            return

        changed = False
        server_agent_id = str(agent_data.get("agent_id") or "").strip()
        server_agent_name = str(agent_data.get("agent_name") or "").strip()

        if server_agent_id and server_agent_id != self.config.agent_id:
            self.config.agent_id = server_agent_id
            changed = True

        if server_agent_name and server_agent_name != self.config.agent_name:
            self.config.agent_name = server_agent_name
            changed = True

        if changed:
            save_local_config(persistable_config_snapshot(self.config))
            print(f"[INFO] Saved enrolled identity to {CONFIG_FILE}")

    def register(self, verbose: bool = True) -> bool:
        LOGGER.info("Startup step: registration started")
        payload = {
            "agent_id": self.config.agent_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "registered_at_utc": self.utc_now_iso(),
            "last_seen_utc": self.utc_now_iso(),
            "monitored_paths": self.config.scan_paths,
            "monitor_status": self.monitor_status,
            "monitor_message": self.monitor_message,
        }
        if self.config.agent_name:
            payload["agent_name"] = self.config.agent_name
        if self.config.agent_port:
            payload["port"] = self.config.agent_port
        if self.config.enrollment_token:
            payload["enrollment_token"] = self.config.enrollment_token
        registration_response = self._post_json("/api/agents/register", payload, warn=verbose)
        if registration_response is None:
            self._registered = False
            if verbose:
                print(f"[WARN] Agent registration failed: {self.config.agent_id}")
            return False

        self._persist_server_identity(registration_response)
        if not self._registered:
            print(f"[INFO] Agent registered: {self.config.agent_id}")
        self._registered = True
        return True

    def _is_monitored_file(self, file_path: str) -> bool:
        if not self._monitored_files:
            return True
        return _normalized_path_key(file_path) in self._monitored_files

    def _remember_recent_file_event(self, file_path: str, event_type: str) -> None:
        now = time.monotonic()
        with self._recent_file_events_lock:
            self._recent_file_events[file_path] = (event_type, now)
            cutoff = now - MODIFIED_SUPPRESS_WINDOW_SECONDS
            self._recent_file_events = {
                path: data for path, data in self._recent_file_events.items() if data[1] >= cutoff
            }

    def _recent_create_or_delete(self, file_path: str) -> bool:
        now = time.monotonic()
        with self._recent_file_events_lock:
            recent = self._recent_file_events.get(file_path)
            if not recent:
                return False
            event_type, event_ts = recent
            if now - event_ts > MODIFIED_SUPPRESS_WINDOW_SECONDS:
                self._recent_file_events.pop(file_path, None)
                return False
            return event_type in {"created", "deleted"}

    def send_heartbeat(self) -> None:
        payload = {
            "agent_id": self.config.agent_id,
            "timestamp_utc": self.utc_now_iso(),
            "monitor_status": self.monitor_status,
            "monitor_message": self.monitor_message,
        }
        self._post("/api/agents/heartbeat", payload, warn=False)

    def heartbeat_loop(self) -> None:
        while self._running:
            if self._registered:
                self.send_heartbeat()
            time.sleep(self.config.heartbeat_seconds)

    def recovery_loop(self) -> None:
        # Background recovery loop keeps trying to register and flush offline buffer.
        while self._running:
            if not self._registered:
                self.register(verbose=False)

            if self._registered:
                pending = self._buffered_event_count()
                if pending > 0:
                    print(f"[INFO] Attempting to flush {pending} buffered event(s)...")
                self.flush_buffered_events()

            time.sleep(max(5, min(self.config.poll_seconds, 30)))

    def build_initial_baseline(self) -> None:
        baseline_hashes: Dict[str, Optional[str]] = {}
        self._active_scan_paths = []

        LOGGER.info("Startup step: monitor paths detected (%d)", len(self.config.scan_paths or []))

        valid_paths, assessments = validate_monitor_paths(self.config.scan_paths, self.runtime_context)
        for assessment in assessments:
            for warning in assessment.warnings:
                LOGGER.warning("Monitor path warning [%s]: %s", assessment.raw_path, warning)
                print(f"[WARN] {assessment.raw_path}: {warning}")

        self._active_scan_paths = valid_paths
        previous_snapshot = self._load_hash_snapshot()
        previous_scan_paths = self._scan_paths_signature(previous_snapshot.get("scan_paths") or [])
        current_scan_paths = self._scan_paths_signature(self._active_scan_paths)
        if not self._active_scan_paths:
            self._set_monitor_status("degraded", "No valid monitored paths")
        else:
            self._set_monitor_status("ok", "")
            LOGGER.info("Monitor path validation kept %d path(s)", len(self._active_scan_paths))

        for path in self._active_scan_paths:
            try:
                for root, _, files in os.walk(path):
                    for name in files:
                        file_path = os.path.join(root, name)
                        if not self._is_monitored_file(file_path):
                            continue
                        baseline_hashes[file_path] = self.sha256_file(file_path)
            except OSError as exc:
                print(f"[WARN] Failed baseline scan for {path}: {exc}")

        with self.hashes_lock:
            self.file_hashes = baseline_hashes

        previous_hashes = previous_snapshot.get("file_hashes") if isinstance(previous_snapshot, dict) else {}
        replay_count = 0
        if isinstance(previous_hashes, dict) and previous_hashes:
            if previous_scan_paths and previous_scan_paths != current_scan_paths:
                LOGGER.info("Skipping downtime replay because monitored paths changed since last snapshot")
            else:
                replay_count = self._replay_downtime_changes(previous_hashes, baseline_hashes)
                if replay_count:
                    print(f"[INFO] Replayed {replay_count} change(s) detected while agent was offline.")

        self._save_hash_snapshot(baseline_hashes)
        print(f"[INFO] Baseline loaded with {len(self.file_hashes)} files")

    def scan_for_changes(self) -> None:
        current_hashes: Dict[str, Optional[str]] = {}
        scan_paths = self._active_scan_paths or self.config.scan_paths
        for path in scan_paths:
            if not os.path.exists(path):
                continue
            try:
                for root, _, files in os.walk(path):
                    for name in files:
                        file_path = os.path.abspath(os.path.join(root, name))
                        if not self._is_monitored_file(file_path):
                            continue
                        current_hashes[file_path] = self.sha256_file(file_path)
            except OSError as exc:
                print(f"[WARN] Failed change scan for {path}: {exc}")

        with self.hashes_lock:
            previous_hashes = dict(self.file_hashes)

        previous_paths = set(previous_hashes)
        current_paths = set(current_hashes)

        for file_path in current_paths - previous_paths:
            self.send_event("created", file_path, None, current_hashes[file_path])

        for file_path in previous_paths - current_paths:
            self.send_event("deleted", file_path, previous_hashes[file_path], None)

        for file_path in current_paths & previous_paths:
            old_hash = previous_hashes[file_path]
            new_hash = current_hashes[file_path]
            if old_hash != new_hash:
                self.send_event("modified", file_path, old_hash, new_hash)

        with self.hashes_lock:
            self.file_hashes = current_hashes
        self._save_hash_snapshot(current_hashes)

    def baseline_scan_loop(self) -> None:
        while self._running:
            time.sleep(self.config.poll_seconds)
            self.scan_for_changes()

    def send_event(
        self,
        event_type: str,
        file_path: str,
        hash_before: Optional[str],
        hash_after: Optional[str],
        replayed_offline: bool = False,
    ) -> None:
        payload = {
            "agent_id": self.config.agent_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "timestamp_utc": self.utc_now_iso(),
            "file_path": os.path.abspath(file_path),
            "event_type": event_type,
            "hash_before": hash_before,
            "hash_after": hash_after,
            "replayed_offline": bool(replayed_offline),
        }
        if not self._post("/api/events", payload, warn=False):
            queued_count = self._append_buffered_event(payload)
            if queued_count == 1 or queued_count % 50 == 0:
                print(f"[WARN] Server offline. Buffered events: {queued_count}")
        print(self._colored_event_line(event_type, os.path.abspath(file_path)))

    def handle_file_event(self, event_type: str, file_path: str) -> None:
        file_path = os.path.abspath(file_path)
        if not self._is_monitored_file(file_path):
            return
        with self.hashes_lock:
            previous_hash = self.file_hashes.get(file_path)

        if event_type == "created":
            current_hash = self.sha256_file(file_path)
            with self.hashes_lock:
                self.file_hashes[file_path] = current_hash
            self.send_event("created", file_path, None, current_hash)
            self._remember_recent_file_event(file_path, "created")
            return

        if event_type == "modified":
            if not os.path.exists(file_path):
                return
            if self._recent_create_or_delete(file_path):
                return
            current_hash = self.sha256_file(file_path)
            if current_hash is None:
                return
            if previous_hash == current_hash:
                return
            with self.hashes_lock:
                self.file_hashes[file_path] = current_hash
            self.send_event("modified", file_path, previous_hash, current_hash)
            return

        if event_type == "deleted":
            with self.hashes_lock:
                self.file_hashes.pop(file_path, None)
            self.send_event("deleted", file_path, previous_hash, None)
            self._remember_recent_file_event(file_path, "deleted")

    def start_watchers(self) -> None:
        handler = AgentEventHandler(self)
        watch_paths = self._active_scan_paths or self.config.scan_paths
        scheduled_count = 0
        LOGGER.info("Startup step: watcher initialization started")
        for path in watch_paths:
            if not os.path.isdir(path):
                print(f"[WARN] Skipping watcher for missing path: {path}")
                continue
            try:
                self.observer.schedule(handler, path, recursive=True)
                scheduled_count += 1
                print(f"[INFO] Watching: {path}")
            except OSError as exc:
                print(f"[WARN] Failed to watch {path}: {exc}")

        if scheduled_count == 0:
            print("[WARN] No active watcher paths could be started.")
            self._set_monitor_status("degraded", "No valid monitored paths")
            self._watchers_started = False
            return

        try:
            self.observer.start()
            self._watchers_started = True
        except Exception as exc:
            self._watchers_started = False
            self._set_monitor_status("degraded", f"Watcher initialization failed: {exc}")
            LOGGER.exception("Watcher startup failed")

    def run(self) -> None:
        runtime_platform = "windows" if self.runtime_context.is_windows else "linux" if self.runtime_context.is_linux else os.name
        LOGGER.info(
            "Startup step: runtime context identity=%s platform=%s is_system=%s is_root=%s",
            self.runtime_context.identity,
            runtime_platform,
            self.runtime_context.is_system,
            self.runtime_context.is_root,
        )
        if not self.config.scan_paths:
            print("[ERROR] No monitor paths configured.")
            print("[HINT] Rebuild/deploy the agent package from the dashboard so config.json includes monitor paths.")
            self._set_monitor_status("degraded", "No monitor paths configured")

        pending_events = self._buffered_event_count()
        if pending_events > 0:
            print(f"[INFO] Found {pending_events} buffered event(s) from previous offline period.")

        if self.check_server_connectivity(verbose=True):
            self.register(verbose=True)
            if self._registered and pending_events > 0:
                self.flush_buffered_events()
        else:
            self._set_buffer_mode(True, "Server unreachable at startup. Monitoring continues with local buffering.")
            print("[WARN] Agent started in offline-buffer mode. Events will sync automatically when server returns.")

        self.build_initial_baseline()

        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        LOGGER.info("Startup step: heartbeat loop started")

        poll_thread = threading.Thread(target=self.baseline_scan_loop, daemon=True)
        poll_thread.start()

        recovery_thread = threading.Thread(target=self.recovery_loop, daemon=True)
        recovery_thread.start()

        self.start_watchers()

        print("[INFO] Agent is running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[INFO] Stopping agent...")
            self._running = False
            if self._watchers_started:
                self.observer.stop()
                self.observer.join()


if __name__ == "__main__":
    setup_runtime_logging()
    install_unhandled_exception_logging()
    LOGGER.info("Agent executable startup initiated")

    parser = argparse.ArgumentParser(description="FIM Agent")
    parser.add_argument("--show-config", action="store_true", help="Print active configuration and exit")
    parser.add_argument("--reset-config", action="store_true", help="Delete local saved config before startup")
    args = parser.parse_args()

    if args.reset_config:
        removed = reset_local_config()
        if removed:
            print(f"[INFO] Deleted local config: {CONFIG_FILE}")
        else:
            print(f"[INFO] No local config to delete: {CONFIG_FILE}")

    if args.show_config:
        try:
            configuration = build_agent_config()
        except RuntimeError as exc:
            print(f"[ERROR] {exc}")
            LOGGER.error("Configuration load failed: %s", exc)
            sys.exit(1)
        print(json.dumps(effective_config_snapshot(configuration), indent=2))
    else:
        # Runtime startup sequence for service/task/EXE deployment:
        # load dashboard-generated config -> register/enroll -> start monitoring loops.
        try:
            configuration = build_agent_config()
            LOGGER.info("Startup step: config file loaded and parsed successfully")
        except RuntimeError as exc:
            print(f"[ERROR] {exc}")
            LOGGER.error("Configuration load failed: %s", exc)
            sys.exit(1)
        try:
            agent = FIMAgent(configuration)
            agent.run()
        except Exception:
            LOGGER.error("Agent runtime failed with unhandled exception:\n%s", traceback.format_exc())
            raise
