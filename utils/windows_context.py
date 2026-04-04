import ctypes
import getpass
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


DRIVE_REMOTE = 4
LINUX_REMOTE_FS_TYPES = {
    "9p",
    "afp",
    "autofs",
    "ceph",
    "cifs",
    "davfs",
    "fuse.sshfs",
    "glusterfs",
    "lustre",
    "nfs",
    "nfs4",
    "smbfs",
    "sshfs",
}


@dataclass
class RuntimeContext:
    username: str = ""
    domain: str = ""
    identity: str = ""
    is_system: bool = False
    is_windows: bool = False
    is_linux: bool = False
    is_root: bool = False
    uid: int = -1
    gid: int = -1
    home_dir: str = ""


# Backward-compatible alias used by existing imports/type annotations.
WindowsRuntimeContext = RuntimeContext


@dataclass
class MonitorPathAssessment:
    raw_path: str
    normalized_path: str
    exists: bool
    accessible: bool
    user_profile_path: bool
    onedrive_path: bool
    mapped_or_network_path: bool
    valid_for_monitoring: bool
    warnings: List[str] = field(default_factory=list)


def _is_windows_platform() -> bool:
    return os.name == "nt"


def _is_linux_platform() -> bool:
    return sys.platform.startswith("linux")


def _drive_type_is_remote(path: str) -> bool:
    if not _is_windows_platform():
        return False

    drive, _ = os.path.splitdrive(path)
    if not drive:
        return path.startswith("\\\\")

    root = f"{drive}\\"
    try:
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
    except Exception:
        return False
    return drive_type == DRIVE_REMOTE


def _decode_linux_mount_token(token: str) -> str:
    return (
        str(token or "")
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _linux_mount_type_for_path(path: str) -> Optional[str]:
    if not _is_linux_platform() or not path:
        return None

    normalized = os.path.abspath(path)
    best_match = ""
    best_fs_type = None

    try:
        with open("/proc/mounts", "r", encoding="utf-8") as mount_file:
            for raw_line in mount_file:
                parts = raw_line.split()
                if len(parts) < 3:
                    continue
                mount_point = _decode_linux_mount_token(parts[1])
                fs_type = parts[2].lower()

                if not mount_point:
                    continue
                if normalized == mount_point or normalized.startswith(mount_point.rstrip("/") + "/"):
                    if len(mount_point) > len(best_match):
                        best_match = mount_point
                        best_fs_type = fs_type
    except OSError:
        return None

    return best_fs_type


def _linux_path_is_remote(path: str) -> bool:
    fs_type = _linux_mount_type_for_path(path)
    return bool(fs_type and fs_type in LINUX_REMOTE_FS_TYPES)


def _path_is_remote(path: str) -> bool:
    if _is_windows_platform():
        return _drive_type_is_remote(path)
    if _is_linux_platform():
        return _linux_path_is_remote(path)
    return False


def detect_runtime_context() -> RuntimeContext:
    if _is_windows_platform():
        username = str(os.environ.get("USERNAME") or "").strip()
        domain = str(os.environ.get("USERDOMAIN") or "").strip()

        try:
            size = ctypes.c_uint(257)
            buffer = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
                if buffer.value.strip():
                    username = buffer.value.strip()
        except Exception:
            pass

        identity = f"{domain}\\{username}" if domain and username else username
        is_system = username.upper() == "SYSTEM"

        return RuntimeContext(
            username=username,
            domain=domain,
            identity=identity,
            is_system=is_system,
            is_windows=True,
            is_linux=False,
            is_root=False,
            uid=-1,
            gid=-1,
            home_dir="",
        )

    uid = -1
    gid = -1
    is_root = False
    try:
        uid = int(os.geteuid())
        gid = int(os.getegid())
        is_root = uid == 0
    except (AttributeError, OSError, ValueError):
        pass

    username = str(os.environ.get("USER") or "").strip()
    if not username:
        try:
            username = getpass.getuser()
        except Exception:
            username = ""

    home_dir = str(os.path.expanduser("~") or "").strip()
    identity = username or str(uid)

    return RuntimeContext(
        username=username,
        domain="",
        identity=identity,
        is_system=False,
        is_windows=False,
        is_linux=_is_linux_platform(),
        is_root=is_root,
        uid=uid,
        gid=gid,
        home_dir=home_dir,
    )


def detect_windows_runtime_context() -> RuntimeContext:
    # Backward-compatible function name.
    return detect_runtime_context()


def _normalize_path_token(path: str) -> str:
    token = str(path or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1].strip()
    token = os.path.expandvars(os.path.expanduser(token))
    return os.path.abspath(token) if token else ""


def _is_user_profile_path(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:\\Users\\[^\\]+(\\|$)", path, flags=re.IGNORECASE))


def _is_onedrive_path(path: str) -> bool:
    return "\\onedrive\\" in path.lower()


def _is_user_desktop_or_documents(path: str) -> bool:
    lowered = path.lower()
    return "\\users\\" in lowered and ("\\desktop\\" in lowered or "\\documents\\" in lowered)


def _is_linux_user_home_path(path: str, context: RuntimeContext) -> bool:
    if not context.is_linux:
        return False

    normalized = os.path.abspath(path)
    home = str(context.home_dir or "").rstrip("/")
    if home and (normalized == home or normalized.startswith(home + "/")):
        return True
    return normalized.startswith("/home/") or normalized.startswith("/run/user/")


def path_requires_user_context(path: str, context: Optional[RuntimeContext] = None) -> bool:
    normalized = _normalize_path_token(path)
    runtime_context = context or detect_runtime_context()

    if runtime_context.is_windows:
        return _is_user_profile_path(normalized) or _is_onedrive_path(normalized) or _path_is_remote(normalized)

    if runtime_context.is_linux:
        return _is_linux_user_home_path(normalized, runtime_context) or _path_is_remote(normalized)

    return False


def _is_accessible_path(path: str) -> bool:
    try:
        if os.path.isdir(path):
            with os.scandir(path):
                pass
            return True
        if os.path.isfile(path):
            with open(path, "rb"):
                pass
            return True
        # Special files that are neither regular file nor directory.
        return os.access(path, os.R_OK)
    except OSError:
        return False


def assess_monitor_path(path: str, context: RuntimeContext) -> MonitorPathAssessment:
    normalized = _normalize_path_token(path)
    warnings: List[str] = []

    exists = bool(normalized) and os.path.exists(normalized)
    accessible = _is_accessible_path(normalized) if exists else False

    user_profile_path = _is_user_profile_path(normalized)
    onedrive_path = _is_onedrive_path(normalized)
    mapped_or_network_path = _path_is_remote(normalized)
    linux_user_home_path = _is_linux_user_home_path(normalized, context)

    if not normalized:
        warnings.append("Path is empty after normalization.")
    if normalized and not exists:
        warnings.append("Path does not exist.")
    if exists and not accessible:
        warnings.append("Path exists but is not accessible.")

    if context.is_windows and context.is_system:
        if user_profile_path or onedrive_path:
            warnings.append("Configured path may be invalid under SYSTEM context.")
        if _is_user_desktop_or_documents(normalized):
            warnings.append("Path targets user Desktop/Documents which is risky under SYSTEM context.")
        if mapped_or_network_path:
            warnings.append("Configured path appears to be a mapped/network location under SYSTEM context.")

    if context.is_linux:
        if mapped_or_network_path:
            warnings.append("Path appears to be on a network filesystem and may be intermittently unavailable.")
        if context.is_root and linux_user_home_path:
            warnings.append("Running as root while monitoring user-home paths can cause ownership/visibility surprises.")
        if not context.is_root and exists:
            required_mode = os.R_OK | (os.X_OK if os.path.isdir(normalized) else 0)
            if not os.access(normalized, required_mode):
                warnings.append("Path is not readable by the current Linux service user.")

    valid_for_monitoring = exists and accessible
    if context.is_windows and context.is_system and (user_profile_path or onedrive_path or mapped_or_network_path):
        valid_for_monitoring = False
    if context.is_linux and not context.is_root and exists:
        required_mode = os.R_OK | (os.X_OK if os.path.isdir(normalized) else 0)
        if not os.access(normalized, required_mode):
            valid_for_monitoring = False

    return MonitorPathAssessment(
        raw_path=str(path or ""),
        normalized_path=normalized,
        exists=exists,
        accessible=accessible,
        user_profile_path=user_profile_path,
        onedrive_path=onedrive_path,
        mapped_or_network_path=mapped_or_network_path,
        valid_for_monitoring=valid_for_monitoring,
        warnings=warnings,
    )


def validate_monitor_paths(paths: List[str], context: RuntimeContext) -> Tuple[List[str], List[MonitorPathAssessment]]:
    valid_paths: List[str] = []
    assessments: List[MonitorPathAssessment] = []

    for path in paths or []:
        assessment = assess_monitor_path(path, context)
        assessments.append(assessment)
        if assessment.valid_for_monitoring:
            valid_paths.append(assessment.normalized_path)

    deduped_valid_paths: List[str] = []
    seen = set()
    for path in valid_paths:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        deduped_valid_paths.append(path)

    return deduped_valid_paths, assessments
