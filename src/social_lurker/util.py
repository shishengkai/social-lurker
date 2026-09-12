"""Bounded serialization, safe paths and short OS locks."""

import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .errors import LurkerError, require

_local = threading.local()
RANK = {"poll.lock": 1, "request.lock": 2, "state.lock": 3}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse_json(raw, maximum=1024 * 1024):
    require(len(raw.encode() if isinstance(raw, str) else raw) <= maximum, "INPUT_TOO_LARGE")

    def unique(pairs):
        obj = {}
        for k, v in pairs:
            require(k not in obj, "JSON_DUPLICATE_KEY")
            obj[k] = v
        return obj

    def invalid(_):
        raise LurkerError("JSON_INVALID")

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise LurkerError("JSON_INVALID") from None


def ident():
    return str(uuid.uuid4())


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def safe_path(path):
    p = Path(path).absolute()
    require(p != Path(p.anchor) and ".." not in p.parts, "UNSAFE_PATH")
    require(not any(x.is_symlink() for x in (p, *p.parents)), "SYMLINK_REJECTED")
    return p


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path, data, mode=0o600):
    path = safe_path(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, (canonical(value) + "\n").encode())


@contextlib.contextmanager
def lock(root, name, timeout=5):
    path = safe_path(Path(root) / name)
    stack = getattr(_local, "locks", [])
    require(not stack or RANK[name] > stack[-1], "LOCK_ORDER_INVALID")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    started = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise LurkerError("INSTANCE_BUSY", "实例忙，请稍后再试", retryable=True) from None
                time.sleep(0.02)
        _local.locks = stack + [RANK[name]]
        try:
            yield
        finally:
            _local.locks = stack
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def public_url(value, domains=None):
    if not isinstance(value, str) or not value or len(value) > 8192 or any(c.isspace() for c in value):
        return None
    try:
        u = urlsplit(value)
        host = u.hostname or ""
        if u.scheme != "https" or u.username or u.password or u.port not in (None, 443) or not host:
            return None
        if any(unicodedata.category(c).startswith("C") for c in value) or "\\" in value:
            return None
        if host == "localhost" or "." not in host or host.endswith((".local", ".internal", ".localhost")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if domains and not any(host == d or host.endswith("." + d) for d in domains):
            return None
        if any(
            re.search(r"(?i)(token|cookie|authorization|api.?key|credential|password)", k)
            for k, _ in parse_qsl(u.query)
        ):
            return None
        return value
    except ValueError:
        return None


def public_cover_url(value):
    """Allow a public Tencent image's resource signature, never an account credential.

    The ordinary source-link and credential URL policy stays unchanged. These signatures
    are returned alongside public covers and authorize just that image, not a TikHub API.
    """
    if public_url(value):
        return value
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        u = urlsplit(value)
        if u.hostname != "wxapp.tc.qq.com":
            return None
        pairs = parse_qsl(u.query, keep_blank_values=True)
        signatures = [v for k, v in pairs if k == "token"]
        if len(signatures) != 1 or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,2048}", signatures[0]):
            return None
        from urllib.parse import urlencode

        unsigned = u._replace(query=urlencode([(k, v) for k, v in pairs if k != "token"])).geturl()
        if public_url(unsigned) and not any(c.isspace() for c in value) and "\\" not in value:
            return value
    except ValueError:
        pass
    return None


def clean_text(value, maximum):
    require(isinstance(value, str), "METADATA_INVALID")
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C") or c in "\n\t")
    return " ".join(value.split())[:maximum]


def platform_id(value):
    require(
        (isinstance(value, str) and 0 < len(value) <= 512) or (type(value) is int and value > 0),
        "IDENTITY_INVALID",
    )
    text = str(value)
    require(not any(unicodedata.category(c).startswith("C") or c.isspace() for c in text), "IDENTITY_INVALID")
    return text


def semver(value):
    require(
        isinstance(value, str) and re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value),
        "VERSION_INVALID",
    )
    return tuple(map(int, value.split(".")))
