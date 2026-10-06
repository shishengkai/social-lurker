import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .errors import LurkerError


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_tree(root):
    root = Path(root)
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as f:
                os.fsync(f.fileno())
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        sync_dir(path)
    sync_dir(root)
    sync_dir(root.parent)


def atomic_json(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read_json(path, code="CONFIG_INVALID"):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise LurkerError(code) from None
