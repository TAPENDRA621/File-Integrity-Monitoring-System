import argparse
import hashlib
import json
import os
import platform
import socket
import threading
import time
from dataclasses import dataclass
from datetime import timezone, datetime
from typing import Dict, List, Optional

import requests
from colorama import Fore, Style, init
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

init(autoreset=True)


def _deduplicate_paths(paths: List[str]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for path in paths:
        normalized = os.path.abspath(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def load_local_config() -> Optional[Dict]:
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file_handle:
            loaded = json.load(file_handle)
            return loaded if isinstance(loaded, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_local_config(config_data: Dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as file_handle:
        json.dump(config_data, file_handle, indent=2)


def _input_non_empty(prompt_text: str, default_value: Optional[str] = None) -> str:
    while True:
        raw = input(prompt_text).strip()
        if raw:
            return raw
        if default_value is not None:
            return default_value
        print("[WARN] Value is required.")


def _input_port(prompt_text: str, default_port: int = 5000) -> int:
    while True:
        raw = input(prompt_text).strip()
        if not raw:
            return default_port
        if raw.isdigit() and 1 <= int(raw) <= 65535:
            return int(raw)
        print("[WARN] Enter a valid port between 1 and 65535.")


def _input_optional_port(prompt_text: str, default_port: Optional[int] = None) -> Optional[int]:
    while True:
        raw = input(prompt_text).strip()
        if not raw:
            return default_port
        if raw.isdigit() and 1 <= int(raw) <= 65535:
            return int(raw)
        print("[WARN] Enter a valid port between 1 and 65535, or leave blank.")


def _input_server_base_url(default_value: str) -> str:
    while True:
        value = _input_non_empty(f"Server base URL [{default_value}]: ", default_value=default_value)
        parsed = value.strip().rstrip("/")
        if parsed.startswith("http://") or parsed.startswith("https://"):
            return parsed
        print("[WARN] Enter a valid URL starting with http:// or https://")


def _default_demo_path() -> str:
    return r"C:\\test_monitor" if os.name == "nt" else "/tmp/test_monitor"


def _select_monitor_mode() -> str:
    print("\nSelect monitoring mode:")
    print("  1) Monitor single file")
    print("  2) Monitor single directory")
    print("  3) Monitor multiple paths")
    while True:
        choice = input("Enter option (1/2/3): ").strip()
        if choice == "1":
            return "single_file"
        if choice == "2":
            return "single_directory"
        if choice == "3":
            return "multiple_paths"
        print("[WARN] Please enter 1, 2, or 3.")


def _prompt_existing_path(prompt_text: str, expected: str = "any", default_value: Optional[str] = None) -> str:
    while True:
        raw_value = _input_non_empty(prompt_text, default_value=default_value)
        candidate = os.path.abspath(os.path.expanduser(raw_value))
        if not os.path.exists(candidate):
            print(f"[WARN] Path does not exist: {candidate}")
            continue

        if expected == "file" and not os.path.isfile(candidate):
            print(f"[WARN] Expected a file path: {candidate}")
            continue
        if expected == "directory" and not os.path.isdir(candidate):
            print(f"[WARN] Expected a directory path: {candidate}")
            continue

        return candidate


def _collect_monitor_targets(mode: str) -> Dict[str, List[str]]:
    scan_paths: List[str] = []
    monitored_files: List[str] = []
    monitor_targets: List[str] = []

    if mode == "single_file":
        file_path = _prompt_existing_path(
            f"File path to monitor [{_default_demo_path()}]: ",
            expected="file",
            default_value=_default_demo_path(),
        )
        absolute_file = os.path.abspath(file_path)
        monitored_files.append(absolute_file)
        scan_paths.append(os.path.dirname(absolute_file) or os.getcwd())
        monitor_targets.append(absolute_file)
    elif mode == "single_directory":
        directory_path = _prompt_existing_path(
            f"Directory path to monitor [{_default_demo_path()}]: ",
            expected="directory",
            default_value=_default_demo_path(),
        )
        absolute_directory = os.path.abspath(directory_path)
        scan_paths.append(absolute_directory)
        monitor_targets.append(absolute_directory)
    else:
        while True:
            raw_paths = _input_non_empty("Multiple paths (comma-separated): ")
            candidates = [
                os.path.abspath(os.path.expanduser(token.strip()))
                for token in raw_paths.split(",")
                if token.strip()
            ]
            if not candidates:
                print("[WARN] Please provide at least one path.")
                continue

            missing = [path for path in candidates if not os.path.exists(path)]
            if missing:
                print("[WARN] These paths do not exist:")
                for path in missing:
                    print(f"  - {path}")
                continue

            for absolute_candidate in candidates:
                if os.path.isfile(absolute_candidate):
                    monitored_files.append(absolute_candidate)
                    scan_paths.append(os.path.dirname(absolute_candidate) or os.getcwd())
                else:
                    scan_paths.append(absolute_candidate)
            monitor_targets.extend(candidates)
            break

    scan_paths = _deduplicate_paths(scan_paths)
    monitored_files = _deduplicate_paths(monitored_files)
    monitor_targets = _deduplicate_paths(monitor_targets)
    return {
        "scan_paths": scan_paths,
        "monitored_files": monitored_files,
        "monitor_targets": monitor_targets,
    }


def run_setup_wizard(default_agent_id: str) -> Dict:
    print("\n=== FIM Agent Setup Wizard ===")
    default_base_url = os.environ.get("FIM_SERVER_BASE_URL", "http://127.0.0.1:5000")
    server_base_url = _input_server_base_url(default_base_url)
    agent_name = _input_non_empty("Agent name (display label): ", default_value=default_agent_id)
    agent_id = _input_non_empty(f"Agent ID [{default_agent_id}]: ", default_value=default_agent_id)
    agent_port = _input_optional_port("Agent port (optional): ", default_port=None)
    mode = _select_monitor_mode()
    monitor_config = _collect_monitor_targets(mode)

    config_data = {
        "server_base_url": server_base_url,
        "agent_name": agent_name,
        "agent_id": agent_id,
        "agent_port": agent_port,
        "monitor_mode": mode,
        "scan_paths": monitor_config["scan_paths"],
        "monitored_files": monitor_config["monitored_files"],
        "monitor_targets": monitor_config["monitor_targets"],
    }
    save_local_config(config_data)
    print(f"[INFO] Configuration saved to: {CONFIG_FILE}")
    return config_data


def reset_local_config() -> bool:
    if not os.path.exists(CONFIG_FILE):
        return False
    os.remove(CONFIG_FILE)
    return True


def build_agent_config(reconfigure: bool) -> "AgentConfig":
    existing = load_local_config()
    if reconfigure or existing is None:
        if not os.isatty(0):
            print("[WARN] Interactive setup unavailable; using environment/default configuration.")
            return AgentConfig()
        existing = run_setup_wizard(default_agent_id=os.environ.get("FIM_AGENT_ID", socket.gethostname()))

    return AgentConfig(
        server_base_url=existing.get("server_base_url", os.environ.get("FIM_SERVER_BASE_URL", "http://localhost:5000")),
        agent_name=existing.get("agent_name", os.environ.get("FIM_AGENT_NAME", socket.gethostname())),
        agent_id=existing.get("agent_id", os.environ.get("FIM_AGENT_ID", socket.gethostname())),
        agent_port=existing.get("agent_port", os.environ.get("FIM_AGENT_PORT")),
        heartbeat_seconds=int(existing.get("heartbeat_seconds", os.environ.get("FIM_HEARTBEAT_SECONDS", "30"))),
        poll_seconds=int(existing.get("poll_seconds", os.environ.get("FIM_POLL_SECONDS", "15"))),
        monitor_mode=existing.get("monitor_mode", "multiple_paths"),
        monitor_targets=existing.get("monitor_targets"),
        scan_paths=existing.get("scan_paths"),
        monitored_files=existing.get("monitored_files"),
    )


def effective_config_snapshot(config: "AgentConfig") -> Dict:
    return {
        "server_base_url": config.server_base_url,
        "agent_name": config.agent_name,
        "agent_id": config.agent_id,
        "agent_port": config.agent_port,
        "heartbeat_seconds": config.heartbeat_seconds,
        "poll_seconds": config.poll_seconds,
        "monitor_mode": config.monitor_mode,
        "monitor_targets": config.monitor_targets,
        "scan_paths": config.scan_paths,
        "monitored_files": config.monitored_files,
        "config_file": CONFIG_FILE,
    }


@dataclass
class AgentConfig:
    server_base_url: str = os.environ.get("FIM_SERVER_BASE_URL", "http://localhost:5000")
    agent_name: str = os.environ.get("FIM_AGENT_NAME", socket.gethostname())
    agent_id: str = os.environ.get("FIM_AGENT_ID", socket.gethostname())
    agent_port: Optional[int] = None
    heartbeat_seconds: int = int(os.environ.get("FIM_HEARTBEAT_SECONDS", "30"))
    poll_seconds: int = int(os.environ.get("FIM_POLL_SECONDS", "15"))
    monitor_mode: str = "multiple_paths"
    monitor_targets: List[str] = None
    scan_paths: List[str] = None
    monitored_files: List[str] = None

    def __post_init__(self):
        if self.scan_paths is None:
            default_paths = ",".join([
                os.path.join(".", "test_monitor"),
                os.path.join(".", "important_files"),
            ])
            raw_paths = os.environ.get("FIM_MONITOR_PATHS", default_paths)

            normalized_paths = raw_paths
            if platform.system().lower().startswith("win"):
                normalized_paths = normalized_paths.replace(";", ",")
            else:
                normalized_paths = normalized_paths.replace(os.pathsep, ",")

            self.scan_paths = [
                os.path.abspath(path.strip())
                for path in normalized_paths.split(",")
                if path.strip()
            ]
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
        self.hostname = socket.gethostname()
        self.ip_address = self._get_local_ip()
        self.file_hashes: Dict[str, Optional[str]] = {}
        self.hashes_lock = threading.Lock()
        self.session = requests.Session()
        self.observer = Observer()
        self._running = True
        self._monitored_files = set(self.config.monitored_files or [])

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

    def _post(self, endpoint: str, payload: Dict) -> None:
        url = f"{self.config.server_base_url.rstrip('/')}{endpoint}"
        try:
            response = self.session.post(url, json=payload, timeout=8)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"[WARN] Failed to send {endpoint}: {exc}")

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

    def register(self) -> None:
        payload = {
            "agent_id": self.config.agent_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "registered_at_utc": self.utc_now_iso(),
            "last_seen_utc": self.utc_now_iso(),
            "monitored_paths": self.config.scan_paths,
        }
        if self.config.agent_name:
            payload["agent_name"] = self.config.agent_name
        if self.config.agent_port:
            payload["port"] = self.config.agent_port
        self._post("/api/agents/register", payload)
        print(f"[INFO] Agent registered: {self.config.agent_id}")

    def _is_monitored_file(self, file_path: str) -> bool:
        if not self._monitored_files:
            return True
        return os.path.abspath(file_path) in self._monitored_files

    def send_heartbeat(self) -> None:
        payload = {
            "agent_id": self.config.agent_id,
            "timestamp_utc": self.utc_now_iso(),
        }
        self._post("/api/agents/heartbeat", payload)

    def heartbeat_loop(self) -> None:
        while self._running:
            self.send_heartbeat()
            time.sleep(self.config.heartbeat_seconds)

    def build_initial_baseline(self) -> None:
        baseline_hashes: Dict[str, Optional[str]] = {}
        for path in self.config.scan_paths:
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
            for root, _, files in os.walk(path):
                for name in files:
                    file_path = os.path.join(root, name)
                    if not self._is_monitored_file(file_path):
                        continue
                    baseline_hashes[file_path] = self.sha256_file(file_path)

        with self.hashes_lock:
            self.file_hashes = baseline_hashes
        print(f"[INFO] Baseline loaded with {len(self.file_hashes)} files")

    def scan_for_changes(self) -> None:
        current_hashes: Dict[str, Optional[str]] = {}
        for path in self.config.scan_paths:
            if not os.path.exists(path):
                continue
            for root, _, files in os.walk(path):
                for name in files:
                    file_path = os.path.abspath(os.path.join(root, name))
                    if not self._is_monitored_file(file_path):
                        continue
                    current_hashes[file_path] = self.sha256_file(file_path)

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

    def baseline_scan_loop(self) -> None:
        while self._running:
            time.sleep(self.config.poll_seconds)
            self.scan_for_changes()

    def send_event(self, event_type: str, file_path: str, hash_before: Optional[str], hash_after: Optional[str]) -> None:
        payload = {
            "agent_id": self.config.agent_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "timestamp_utc": self.utc_now_iso(),
            "file_path": os.path.abspath(file_path),
            "event_type": event_type,
            "hash_before": hash_before,
            "hash_after": hash_after,
        }
        self._post("/api/events", payload)
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
            return

        if event_type == "modified":
            current_hash = self.sha256_file(file_path)
            with self.hashes_lock:
                self.file_hashes[file_path] = current_hash
            self.send_event("modified", file_path, previous_hash, current_hash)
            return

        if event_type == "deleted":
            with self.hashes_lock:
                self.file_hashes.pop(file_path, None)
            self.send_event("deleted", file_path, previous_hash, None)

    def start_watchers(self) -> None:
        handler = AgentEventHandler(self)
        for path in self.config.scan_paths:
            self.observer.schedule(handler, path, recursive=True)
            print(f"[INFO] Watching: {path}")
        self.observer.start()

    def run(self) -> None:
        self.register()
        self.build_initial_baseline()

        heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        poll_thread = threading.Thread(target=self.baseline_scan_loop, daemon=True)
        poll_thread.start()

        self.start_watchers()

        print("[INFO] Agent is running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[INFO] Stopping agent...")
            self._running = False
            self.observer.stop()
            self.observer.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FIM Agent")
    parser.add_argument("--reconfigure", action="store_true", help="Run interactive setup wizard and overwrite local config")
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
        configuration = build_agent_config(reconfigure=args.reconfigure)
        print(json.dumps(effective_config_snapshot(configuration), indent=2))
    else:
        configuration = build_agent_config(reconfigure=args.reconfigure)
        agent = FIMAgent(configuration)
        agent.run()
