"""Local calendar slots; no catch-up jobs or elapsed-time schedule drift."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .errors import require


def minute(text):
    require(isinstance(text, str) and len(text) == 5 and text[2] == ":", "SCHEDULE_INVALID")
    try:
        h, m = map(int, text.split(":"))
    except ValueError:
        require(False, "SCHEDULE_INVALID")
    require(0 <= h < 24 and 0 <= m < 60, "SCHEDULE_INVALID")
    return h * 60 + m


def allowed(now, settings):
    local = datetime.fromtimestamp(now, ZoneInfo(settings["timezone"]))
    current = local.hour * 60 + local.minute
    quiet = settings["monitor"]["quiet_hours"]
    start, end = minute(quiet["start"]), minute(quiet["end"])
    inside = start <= current < end if start < end else current >= start or current < end
    return not inside


def slots(now, settings):
    zone = ZoneInfo(settings["timezone"])
    day = datetime.fromtimestamp(now, zone).date()
    interval = settings["monitor"]["interval_seconds"] // 60
    anchor = minute(settings["monitor"]["schedule_anchor"])
    result = set()
    for offset in range(-3, 4):
        date = day + timedelta(days=offset)
        for m in range(anchor % interval, 1440, interval):
            naive = datetime(date.year, date.month, date.day, m // 60, m % 60)
            for fold in (0, 1):
                local = naive.replace(tzinfo=zone, fold=fold)
                timestamp = int(local.timestamp())
                if datetime.fromtimestamp(timestamp, zone).replace(tzinfo=None) != naive:
                    continue
                if allowed(timestamp, settings):
                    result.add(timestamp)
    require(len(result) > 2, "SCHEDULE_INVALID")
    return sorted(result)


def context(now, settings):
    values = slots(now, settings)
    past = [s for s in values if s <= now]
    return past[-1], past[-2]


def next_slot(now, settings):
    return next(s for s in slots(now, settings) if s > now)


def utc_day(now):
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
