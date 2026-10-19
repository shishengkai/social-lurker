"""Small metadata validators shared by platform adapters."""

from datetime import datetime, timezone

from ..errors import require
from .base import public_url


def positive_id(value, code="PAGE_IDENTITY_INVALID"):
    if type(value) is int:
        value = str(value)
    require(
        isinstance(value, str)
        and 0 < len(value) <= 1024
        and value.isascii()
        and value.isdigit()
        and int(value) > 0,
        code,
    )
    return str(int(value))


def flag(value):
    require(type(value) in (bool, int) and value in (0, 1))
    return bool(value)


def cursor_token(value):
    require(isinstance(value, str) and 0 < len(value.encode()) <= 8192, "CURSOR_INVALID")
    return value


def metadata_url(value):
    if isinstance(value, str) and value.startswith("http://"):
        value = "https://" + value[7:]
    return public_url(value)


def iso_timestamp(value):
    # Relative labels and date-only values do not establish an exact publication time.
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.utcoffset() is not None:
            return date.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except ValueError:
        pass
    return None
