from datetime import datetime, timezone

_APPLE_EPOCH_TIMESTAMP = datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp()


def get_datetime(date: float) -> datetime:
    """Convert an absolute Core Data timestamp to a naive local datetime."""
    return datetime.fromtimestamp(date + _APPLE_EPOCH_TIMESTAMP)


def get_date_iso(date: float) -> str:
    return get_datetime(date).date().isoformat()


def get_date(dt: datetime) -> float:
    """Convert an aware instant or naive local datetime to Core Data seconds."""
    return dt.timestamp() - _APPLE_EPOCH_TIMESTAMP
