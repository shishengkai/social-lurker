import calendar
import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .errors import LurkerError, require


def now():
    return time.time()


def token():
    return str(uuid.uuid4())


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        Path(temp).unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, json_text(value) + "\n")


def safe_path(root, relative):
    root = Path(root).resolve()
    rel = Path(relative)
    require(not rel.is_absolute() and ".." not in rel.parts and rel.parts, "UNSAFE_PATH", "拒绝越界路径")
    current = root
    for part in rel.parts:
        current = current / part
        require(not current.is_symlink(), "UNSAFE_PATH", "拒绝符号链接路径")
    require(current.resolve().is_relative_to(root), "UNSAFE_PATH", "拒绝越界路径")
    return current


@contextlib.contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LurkerError("BUSY", "已有执行者，等待下次唤醒") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def time_range(end, amount, unit, timezone):
    require(type(amount) is int and amount > 0, "INVALID_RANGE", "时长必须为正整数")
    require(unit in {"hours", "days", "months", "years"}, "INVALID_RANGE", "不支持的时间单位")
    date = datetime.fromtimestamp(end, ZoneInfo(timezone))
    if unit in {"hours", "days"}:
        start = datetime.fromtimestamp(end, UTC) - timedelta(**{unit: amount})
    else:
        months = amount * (12 if unit == "years" else 1)
        year, month0 = divmod(date.year * 12 + date.month - 1 - months, 12)
        require(year >= 1, "INVALID_RANGE", "时间范围过大")
        month = month0 + 1
        start = date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1]))
    return start.timestamp(), end
