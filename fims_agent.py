import os
import time
import hashlib
import json
import requests
import logging
from datetime import datetime
import threading
import sys
import socket
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import queue

def get_local_ip():
    """Get the local IP address used for outgoing connections."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a dummy address to determine the local IP
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP

# --- Configuration ---
# The central FIMS server can be configured via environment variables so that
# agents on different machines / networks can report to a single dashboard.
# If not provided, we default to the local machine for backwards compatibility.
SERVER_URL = os.environ.get(
    "FIMS_SERVER_URL",
    f"http://localhost:5000/api/logs",
)
API_KEY = os.environ.get("FIMS_API_KEY", "secret-fims-key")

# How often to perform a full verification scan in addition to real-time
# watchdog events (in seconds). The user requested a 1‑minute period.
MONITOR_INTERVAL = int(os.environ.get("FIMS_MONITOR_INTERVAL", "60"))

DIRECTORIES_TO_WATCH = [
    os.path.abspath("./test_monitor"),
    os.path.abspath("./important_files"),
]  # Example paths
EXCLUDE_EXTENSIONS = [".tmp", ".log", ".swp"]

# Ensure monitored directories exist for demo
for d in DIRECTORIES_TO_WATCH:
    if not os.path.exists(d):
        try:
            os.makedirs(d)
        except:
            pass # might just be watching current dir

# Logging Setup
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("FIM_Agent")

class FileChangeHandler(FileSystemEventHandler):
    """Handles file system events for real-time detection"""
    def __init__(self, event_queue, agent):
        self.event_queue = event_queue
        self.agent = agent
    
    def on_modified(self, event):
        if not event.is_directory and not any(event.src_path.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
            self.event_queue.put(('modified', event.src_path))
    
    def on_created(self, event):
        if not event.is_directory and not any(event.src_path.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
            self.event_queue.put(('created', event.src_path))
    
    def on_deleted(self, event):
        if not event.is_directory and not any(event.src_path.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
            self.event_queue.put(('deleted', event.src_path))

class FIMAgent:
    def __init__(self):
        self.baseline = {}
        self.agent_name = socket.gethostname()
        self.running = True
        self.event_queue = queue.Queue()
        self.observer = Observer()
        self.processed_events = {}  # Track processed event timestamps to avoid duplicates

    def calculate_file_hash(self, filepath):
        """Calculates SHA-256 hash of a file."""
        sha256_hash = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                # Read in chunks to avoid memory issues with large files
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except PermissionError:
            return None
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Error hashing {filepath}: {e}")
            return None

    def scan_directory(self, directory):
        """Scans a directory recursively and returns a dict of {filepath: hash}."""
        current_state = {}
        abs_directory = os.path.abspath(directory)
        for root, dirs, files in os.walk(abs_directory):
            for file in files:
                # Skip excluded extensions
                if any(file.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
                    continue
                
                filepath = os.path.join(root, file)
                file_hash = self.calculate_file_hash(filepath)
                if file_hash:
                    current_state[filepath] = file_hash
        return current_state

    def send_log(self, parsed_event):
        """Sends a log entry to the central server."""
        headers = {"Content-Type": "application/json", "x-api-key": API_KEY}
        try:
            response = requests.post(SERVER_URL, json=parsed_event, headers=headers, verify=False) # verify=False for self-signed certs
            if response.status_code == 200:
                logger.info(f"Sent log: {parsed_event['event_type']} -> {parsed_event['file_path']}")
            else:
                logger.error(f"Failed to send log. Status: {response.status_code}")
        except requests.exceptions.ConnectionError:
            logger.warning("Server unreachable. Retrying next cycle...")
        except Exception as e:
            logger.error(f"Error sending log: {e}")

    def run(self):
        logger.info(f"Starting FIMS Agent on {self.agent_name}...")
        
        # Initial scan to build baseline
        logger.info("Building baseline...")
        for d in DIRECTORIES_TO_WATCH:
            if os.path.exists(d):
                self.baseline.update(self.scan_directory(d))
        logger.info(f"Baseline established with {len(self.baseline)} files.")

        # Setup watchdog observer
        handler = FileChangeHandler(self.event_queue, self)
        for directory in DIRECTORIES_TO_WATCH:
            if os.path.exists(directory):
                self.observer.schedule(handler, path=directory, recursive=True)
        
        self.observer.start()
        logger.info("File system observer started for real-time detection")

        # Start event processing thread (real-time events)
        processor_thread = threading.Thread(target=self._process_events, daemon=True)
        processor_thread.start()

        # Start periodic verification thread to ensure that, at least every
        # MONITOR_INTERVAL seconds, the filesystem state is reconciled with the
        # baseline (useful if any watchdog events are missed).
        verifier_thread = threading.Thread(
            target=self._periodic_verification_loop,
            daemon=True,
        )
        verifier_thread.start()

        # Keep the agent running
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down agent...")
            self.running = False
            self.observer.stop()
            self.observer.join()

    def _process_events(self):
        """Process file system events from the queue"""
        while self.running:
            try:
                event_type, filepath = self.event_queue.get(timeout=1)
                
                # Skip excluded extensions
                if any(filepath.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
                    continue
                
                # Debounce rapid events
                event_key = f"{event_type}:{filepath}"
                current_time = time.time()
                if event_key in self.processed_events:
                    continue
                self.processed_events[event_key] = current_time
                
                # Clean up old entries
                self.processed_events = {k: v for k, v in self.processed_events.items() 
                                        if current_time - v < 2}
                
                # Add small delay to ensure file write is complete
                time.sleep(0.1)
                
                # Get current file hash
                current_hash = self.calculate_file_hash(filepath)
                
                if event_type == 'created':
                    if current_hash and filepath not in self.baseline:
                        logger.info(f"Detected new file: {filepath}")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        event = {
                            "agent_name": self.agent_name,
                            "timestamp": timestamp,
                            "severity": "WARNING",
                            "event_type": "FILE_ADDED",
                            "file_path": filepath
                        }
                        self.send_log(event)
                        self.baseline[filepath] = current_hash
                
                elif event_type == 'modified':
                    if current_hash:
                        if filepath not in self.baseline:
                            # File was created
                            logger.info(f"Detected new file: {filepath}")
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            event = {
                                "agent_name": self.agent_name,
                                "timestamp": timestamp,
                                "severity": "WARNING",
                                "event_type": "FILE_ADDED",
                                "file_path": filepath
                            }
                            self.send_log(event)
                            self.baseline[filepath] = current_hash
                        elif self.baseline[filepath] != current_hash:
                            logger.info(f"Detected modified file: {filepath}")
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            event = {
                                "agent_name": self.agent_name,
                                "timestamp": timestamp,
                                "severity": "CRITICAL",
                                "event_type": "FILE_MODIFIED",
                                "file_path": filepath
                            }
                            self.send_log(event)
                            self.baseline[filepath] = current_hash
                
                elif event_type == 'deleted':
                    if filepath in self.baseline:
                        logger.info(f"Detected deleted file: {filepath}")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        event = {
                            "agent_name": self.agent_name,
                            "timestamp": timestamp,
                            "severity": "WARNING",
                            "event_type": "FILE_DELETED",
                            "file_path": filepath
                        }
                        self.send_log(event)
                        del self.baseline[filepath]
            
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing event: {e}")

    def _periodic_verification_loop(self):
        """
        Periodically rescan watched directories and reconcile with the baseline.
        This guarantees that, even if no real-time events are received, any
        changes will be detected within MONITOR_INTERVAL seconds.
        """
        while self.running:
            time.sleep(MONITOR_INTERVAL)
            try:
                current_state = {}
                for d in DIRECTORIES_TO_WATCH:
                    if os.path.exists(d):
                        current_state.update(self.scan_directory(d))

                # Detect deleted files (present in baseline, missing now)
                for filepath in list(self.baseline.keys()):
                    if filepath not in current_state:
                        logger.info(f"[Periodic scan] Detected deleted file: {filepath}")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        event = {
                            "agent_name": self.agent_name,
                            "timestamp": timestamp,
                            "severity": "WARNING",
                            "event_type": "FILE_DELETED",
                            "file_path": filepath,
                        }
                        self.send_log(event)
                        del self.baseline[filepath]

                # Detect new or modified files
                for filepath, current_hash in current_state.items():
                    if filepath not in self.baseline:
                        logger.info(f"[Periodic scan] Detected new file: {filepath}")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        event = {
                            "agent_name": self.agent_name,
                            "timestamp": timestamp,
                            "severity": "WARNING",
                            "event_type": "FILE_ADDED",
                            "file_path": filepath,
                        }
                        self.send_log(event)
                        self.baseline[filepath] = current_hash
                    elif self.baseline[filepath] != current_hash:
                        logger.info(f"[Periodic scan] Detected modified file: {filepath}")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        event = {
                            "agent_name": self.agent_name,
                            "timestamp": timestamp,
                            "severity": "CRITICAL",
                            "event_type": "FILE_MODIFIED",
                            "file_path": filepath,
                        }
                        self.send_log(event)
                        self.baseline[filepath] = current_hash
            except Exception as e:
                logger.error(f"Error in periodic verification loop: {e}")

if __name__ == "__main__":
    # Create a dummy test folder if it doesn't exist so the agent has something to watch immediately
    if not os.path.exists("./test_monitor"):
        os.makedirs("./test_monitor")
        
    start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(f"./test_monitor/startup_{start_time}.txt", "w") as f:
        f.write("FIMS Agent started.")

    agent = FIMAgent()
    try:
        agent.run()
    except KeyboardInterrupt:
        logger.info("Stopping agent...")
