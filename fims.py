import os
import hashlib
import json
import time
import sys
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


WATCH_PATHS = [
    os.path.join(os.getcwd(), "monitored")  # default folder
]
BASELINE_FILE = "baseline.json"
LOG_FILE = "fims_log.json"
VERIFICATION_INTERVAL = 60  # seconds (1 minute)

# ==============================
# COLORS and ICONS
# ==============================
RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"

# ==============================
# HELPER FUNCTIONS
# ==============================
def compute_hash(file_path):
    """Compute SHA256 hash of a file, skip if locked or missing."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except (FileNotFoundError, PermissionError):
        return None

def log_event(severity, message, details=None):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "severity": severity,
        "message": message,
        "details": details or {}
    }
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"Logging error: {e}")

# ==============================
# BASELINE FUNCTIONS
# ==============================
def build_baseline():
    baseline = {}
    for path in WATCH_PATHS:
        for root, _, files in os.walk(path):
            for file in files:
                file_path = os.path.join(root, file)
                file_hash = compute_hash(file_path)
                if file_hash:
                    baseline[file_path] = file_hash
    with open(BASELINE_FILE, "w") as f:
        json.dump(baseline, f, indent=4)
    return baseline

def load_baseline():
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_baseline(baseline):
    with open(BASELINE_FILE, "w") as f:
        json.dump(baseline, f, indent=4)

def verify(baseline):
    modified, deleted, new = [], [], []
    current_files = {}
    for path in WATCH_PATHS:
        for root, _, files in os.walk(path):
            for file in files:
                file_path = os.path.join(root, file)
                file_hash = compute_hash(file_path)
                if file_hash:
                    current_files[file_path] = file_hash

    # Compare with baseline
    for file_path, old_hash in list(baseline.items()):
        if file_path not in current_files:
            deleted.append(file_path)
        elif current_files[file_path] != old_hash:
            modified.append(file_path)

    for file_path, new_hash in current_files.items():
        if file_path not in baseline:
            new.append(file_path)
            baseline[file_path] = new_hash
            save_baseline(baseline)

    return modified, deleted, new

def print_verification_results(modified, deleted, new):
    print("\n======================================================================")
    print("FILE INTEGRITY VERIFICATION REPORT")
    print("======================================================================")
    if not modified and not deleted and not new:
        print(f"{GREEN}✓ All monitored files are unchanged.{RESET}")
        log_event("INFO", "No changes detected.")
    else:
        if modified:
            print(f"{YELLOW}⚠ MODIFIED FILES:{RESET}")
            for f in modified:
                print(f"{YELLOW}  - {f}{RESET}")
            log_event("WARNING", f"{len(modified)} file(s) modified", {"modified_files": modified})
        if deleted:
            print(f"{RED}✗ DELETED FILES:{RESET}")
            for f in deleted:
                print(f"{RED}  - {f}{RESET}")
            log_event("CRITICAL", f"{len(deleted)} file(s) deleted", {"deleted_files": deleted})
        if new:
            print(f"{BLUE}+ NEW FILES (added to baseline):{RESET}")
            for f in new:
                print(f"{BLUE}  - {f}{RESET}")
            log_event("INFO", f"{len(new)} new file(s) detected and added to baseline", {"new_files": new})
    print("======================================================================")

# ==============================
# REAL-TIME MONITORING (Watchdog)
# ==============================
class FIMHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            new_hash = compute_hash(event.src_path)
            if new_hash:
                baseline = load_baseline()
                baseline[event.src_path] = new_hash
                save_baseline(baseline)
            log_event("INFO", f"New file created: {event.src_path}")
            print(f"{BLUE}📄 New file created (baseline updated): {event.src_path}{RESET}")

    def on_deleted(self, event):
        if not event.is_directory:
            baseline = load_baseline()
            if event.src_path in baseline:
                baseline.pop(event.src_path)
                save_baseline(baseline)
            log_event("CRITICAL", f"File deleted: {event.src_path}")
            print(f"{RED}✗ File deleted: {event.src_path}{RESET}")

    def on_modified(self, event):
        if not event.is_directory:
            new_hash = compute_hash(event.src_path)
            baseline = load_baseline()
            old_hash = baseline.get(event.src_path)

            if new_hash and new_hash != old_hash:
                baseline[event.src_path] = new_hash
                save_baseline(baseline)
                log_event("WARNING", f"File modified: {event.src_path}")
                print(f"{YELLOW}⚠ File modified (baseline updated): {event.src_path}{RESET}")
            else:
                # Ignore metadata-only changes
                print(f"{CYAN}ℹ File saved but content unchanged: {event.src_path}{RESET}")

def start_realtime_monitoring():
    observer = Observer()
    for path in WATCH_PATHS:
        observer.schedule(FIMHandler(), path=path, recursive=True)
    observer.start()
    print(f"{CYAN}🔍 Real-time monitoring started...{RESET}")
    return observer

# ==============================
# COUNTDOWN FUNCTION
# ==============================
def countdown_timer(seconds):
    for remaining in range(seconds, 0, -1):
        sys.stdout.write(f"\r{CYAN}⏳ Next verification in {remaining} second(s)...{RESET}")
        sys.stdout.flush()
        time.sleep(1)
    print("\r", end="")

# ==============================
# MAIN FUNCTION
# ==============================
def main():
    print("\n======================================================================")
    print("FILE INTEGRITY MONITORING SYSTEM (FIMS)")
    print("======================================================================")
    print(f"Monitoring paths: {WATCH_PATHS}")
    print(f"Verification interval: {VERIFICATION_INTERVAL} second(s)")
    print("======================================================================\n")

    baseline = load_baseline()
    if not baseline:
        print("No baseline found. Building initial baseline...")
        baseline = build_baseline()
        print(f"{GREEN}✓ Baseline created with {len(baseline)} files.{RESET}")
    else:
        print(f"{GREEN}✓ Loaded baseline with {len(baseline)} files.{RESET}")

    observer = start_realtime_monitoring()

    try:
        iteration = 0
        while True:
            iteration += 1
            print(f"\n{CYAN}We are monitoring... (Verification #{iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}){RESET}")
            modified, deleted, new = verify(baseline)
            print_verification_results(modified, deleted, new)
            countdown_timer(VERIFICATION_INTERVAL)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
        print(f"\n{RED}FIMS stopped by user{RESET}")

if __name__ == "__main__":
    main()
