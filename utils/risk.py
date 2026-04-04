import os
from pathlib import PurePath

CRITICAL_PATH_KEYWORDS = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/boot/",
    "system32",
    "windows/system32",
    "startup",
    "autorun",
    ".ps1",
    ".bat",
    ".cmd",
    ".exe",
    ".dll",
    ".so",
    ".service",
    ".sh",
]

MEDIUM_EXTENSIONS = {
    ".env",
    ".ini",
    ".json",
    ".conf",
    ".config",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".properties",
}

LOW_EXTENSIONS = {
    ".txt",
    ".md",
    ".log",
    ".csv",
    ".tmp",
}


def classify_risk(file_path: str, event_type: str = "") -> str:
    normalized_event_type = (event_type or "").strip().lower()
    if normalized_event_type == "deleted":
        return "HIGH"
    if normalized_event_type == "modified":
        return "MEDIUM"
    if normalized_event_type == "created":
        return "LOW"

    normalized = (file_path or "").lower().replace(os.sep, "/")
    suffix = PurePath(normalized).suffix

    if any(token in normalized for token in CRITICAL_PATH_KEYWORDS):
        return "HIGH"

    if suffix in MEDIUM_EXTENSIONS:
        return "MEDIUM"

    if suffix in LOW_EXTENSIONS:
        return "LOW"

    return "LOW"
