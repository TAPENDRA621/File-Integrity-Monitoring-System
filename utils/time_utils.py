from datetime import datetime, timezone
from typing import Optional

UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime(UTC_FORMAT)


def parse_utc_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in (UTC_FORMAT, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def is_agent_active(last_seen_utc: Optional[str], threshold_seconds: int = 60) -> bool:
    last_seen = parse_utc_timestamp(last_seen_utc)
    if not last_seen:
        return False
    return (utc_now() - last_seen).total_seconds() <= threshold_seconds
