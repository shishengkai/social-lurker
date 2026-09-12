"""Explicit, per-Bot settings, credentials and version-fenced SQLite transactions."""

import contextlib
import copy
import math
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__
from .errors import LurkerError, require
from .schedule import minute, next_slot, utc_day
from .util import atomic_json, ident, lock, parse_json, safe_path, semver

APPLICATION_ID = 1397509425
SCHEMA_VERSION = 1
STAGES = {"prepared", "frozen", "candidate_ready", "switching", "committed", "rolled_back", "done"}
DEFAULTS = {
    "config_version": 1,
    "instance_id": None,
    "app_version": __version__,
    "timezone": "Asia/Shanghai",
    "source": "tikhub",
    "monitor": {
        "interval_seconds": 7200,
        "schedule_anchor": "07:00",
        "quiet_hours": {"start": "00:00", "end": "07:00"},
        "overlap_seconds": 172800,
        "pagination_mode": "all_new_pages",
        "max_scan_age_seconds": 86400,
    },
    "rate_limit": {"requests_per_second": 1, "max_concurrent_requests": 1, "endpoint_overrides": {}},
    "limits": {"poll_seconds": 90, "notifications_per_activation": 20},
    "host": {
        "bot_id": None,
        "routine_id": None,
        "delivery_verified_at": None,
        "quiet_execution_verified_at": None,
        "evidence_ref": None,
    },
}


def validate(settings):
    require(isinstance(settings, dict) and set(settings) == set(DEFAULTS), "CONFIG_INVALID")
    require(settings["config_version"] == 1 and settings["source"] == "tikhub", "CONFIG_VERSION_INVALID")
    require(
        isinstance(settings["instance_id"], str) and 0 < len(settings["instance_id"]) <= 512,
        "INSTANCE_BINDING_REQUIRED",
    )
    semver(settings["app_version"])
    try:
        ZoneInfo(settings["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise LurkerError("TIMEZONE_INVALID") from None
    for group in ("monitor", "rate_limit", "limits", "host"):
        require(
            isinstance(settings[group], dict) and set(settings[group]) == set(DEFAULTS[group]),
            "CONFIG_INVALID",
        )
    monitor = settings["monitor"]
    rate = settings["rate_limit"]
    limits = settings["limits"]
    require(
        type(monitor["interval_seconds"]) is int
        and 60 <= monitor["interval_seconds"] <= 86400
        and monitor["interval_seconds"] % 60 == 0,
        "SCHEDULE_INVALID",
    )
    minute(monitor["schedule_anchor"])
    q = monitor["quiet_hours"]
    require(
        isinstance(q, dict) and set(q) == {"start", "end"} and minute(q["start"]) != minute(q["end"]),
        "SCHEDULE_INVALID",
    )
    require(monitor["pagination_mode"] == "all_new_pages", "CONFIG_INVALID")
    for value, minimum in (
        (monitor["overlap_seconds"], 0),
        (monitor["max_scan_age_seconds"], 1),
        (limits["poll_seconds"], 1),
        (limits["notifications_per_activation"], 1),
    ):
        require(type(value) is int and minimum <= value <= 31536000, "CONFIG_INVALID")

    def rate_ok(value):
        return type(value) in (int, float) and math.isfinite(value) and value > 0

    require(
        rate_ok(rate["requests_per_second"]) and rate["max_concurrent_requests"] == 1, "RATE_CONFIG_INVALID"
    )
    from .sources import ENDPOINTS

    require(isinstance(rate["endpoint_overrides"], dict), "RATE_CONFIG_INVALID")
    for endpoint, value in rate["endpoint_overrides"].items():
        require(
            endpoint in ENDPOINTS and rate_ok(value) and value <= rate["requests_per_second"],
            "RATE_CONFIG_INVALID",
        )
    next_slot(time.time(), settings)
    evidence = settings["host"]["evidence_ref"]
    if evidence is not None:
        require(isinstance(evidence, dict) and evidence.get("schema") == 1, "EVIDENCE_INVALID")
        require(set(evidence) <= {"schema", "host", "sources"}, "EVIDENCE_INVALID")
    return settings


def message_limit(settings):
    evidence = (settings["host"]["evidence_ref"] or {}).get("host", {})
    limit, unit = evidence.get("max_message_length"), evidence.get("length_unit")
    require(
        type(limit) is int
        and limit > 0
        and unit in {"unicode", "utf8", "utf16"}
        and isinstance(evidence.get("length_evidence"), str)
        and bool(evidence["length_evidence"].strip()),
        "HOST_LENGTH_UNVERIFIED",
        "请先登记宿主消息长度与计量单位的真实依据，再获取试发作品",
    )
    return limit, unit


def automatic_gate(settings):
    host = settings["host"]
    e = (host["evidence_ref"] or {}).get("host", {})
    require(
        host["bot_id"]
        and host["routine_id"]
        and host["delivery_verified_at"]
        and host["quiet_execution_verified_at"]
        and e.get("routine_active") is True
        and all(
            e.get(k) is True for k in ("durable_directory", "instance_isolated", "native_schedule_verified")
        ),
        "HOST_AUTOMATIC_UNVERIFIED",
        "后台能力尚待真实验证，当前仅支持前台操作",
    )
    message_limit(settings)


class Instance:
    def __init__(self, path, *, clock=time.time):
        require(
            path is not None and Path(path).is_absolute(),
            "INSTANCE_BINDING_REQUIRED",
            "必须显式指定实例的绝对目录",
        )
        self.root = safe_path(path)
        self.clock = clock
        self.loaded_version = __version__

    def path(self, name):
        result = safe_path(self.root / name)
        require(result.is_relative_to(self.root), "UNSAFE_PATH")
        return result

    def load(self):
        try:
            return validate(parse_json(self.path("settings.json").read_bytes()))
        except FileNotFoundError:
            raise LurkerError("INSTANCE_NOT_INSTALLED", "请先安装独立轻量实例") from None

    def plan(self):
        p = self.path("maintenance.json")
        if not p.exists():
            return None
        try:
            value = parse_json(p.read_bytes(), 8 * 1024 * 1024)
            require(
                isinstance(value, dict) and value.get("protocol") == 1 and value.get("stage") in STAGES,
                "MAINTENANCE_INVALID",
            )
            require(
                type(value.get("business_writes_open")) is bool
                and isinstance(value.get("pending_receipts"), list),
                "MAINTENANCE_INVALID",
            )
            require(value.get("instance_id") == self.load()["instance_id"], "MAINTENANCE_INVALID")
            require(
                isinstance(value.get("plan_id"), str)
                and 0 < len(value["plan_id"]) <= 128
                and "/" not in value["plan_id"]
                and ".." not in value["plan_id"],
                "MAINTENANCE_INVALID",
            )
            require(semver(value["target_version"]) > semver(value["source_version"]), "MAINTENANCE_INVALID")
            import re

            require(
                all(re.fullmatch("[0-9a-f]{40}", value[k]) for k in ("source_sha", "target_sha")),
                "MAINTENANCE_INVALID",
            )
            require(value.get("schema_version") == SCHEMA_VERSION, "MAINTENANCE_INVALID")
            require(
                not value["business_writes_open"] or value["stage"] in {"committed", "rolled_back", "done"},
                "MAINTENANCE_INVALID",
            )
            if value["stage"] != "prepared":
                require(
                    value.get("backup") == "backups/" + value["plan_id"]
                    and isinstance(value.get("old_settings"), dict),
                    "MAINTENANCE_INVALID",
                )
                require(
                    validate(value["old_settings"])["instance_id"] == value["instance_id"],
                    "MAINTENANCE_INVALID",
                )
            for item in value["pending_receipts"]:
                require(
                    isinstance(item, dict)
                    and item.get("state") in {"pending", "applied", "rejected"}
                    and isinstance(item.get("receipt"), dict),
                    "MAINTENANCE_INVALID",
                )
            return value
        except (ValueError, KeyError, TypeError, LurkerError):
            raise LurkerError("MAINTENANCE_INVALID", "维护计划无效，已停止业务写入；请修复计划") from None

    def gate(self, mode="write", *, check_version=True):
        plan = self.plan()
        if plan and not (
            plan["stage"] in {"committed", "rolled_back", "done"} and plan["business_writes_open"]
        ):
            require(
                plan["stage"] == "prepared" and mode in {"read", "write", "result"},
                "MAINTENANCE_ACTIVE",
                "维护中，本次操作尚未执行",
            )
        settings = self.load()
        if check_version:
            require(
                settings["app_version"] == self.loaded_version,
                "VERSION_CHANGED",
                "版本绑定已改变，请从稳定入口重入",
            )
        return settings

    @contextlib.contextmanager
    def transaction(self, mode="write", *, maintenance=False):
        with lock(self.root, "state.lock"):
            settings = self.load() if maintenance else self.gate(mode)
            with self.connection(settings, mode) as db:
                yield db, settings

    @contextlib.contextmanager
    def connection(self, settings, mode="write"):
        # Caller already holds state.lock; used by normal and frozen-receipt paths.
        path = self.path("state.sqlite")
        require(path.is_file(), "DATABASE_MISSING", "数据库不存在；不会自动重建")
        db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            require(
                db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID,
                "DATABASE_IDENTITY_INVALID",
            )
            require(db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, "SCHEMA_UNSUPPORTED")
            require(
                {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                == {"runtime", "watches", "updates"},
                "DATABASE_IDENTITY_INVALID",
            )
            require(
                db.execute("SELECT instance_id FROM runtime WHERE singleton=1").fetchone()[0]
                == settings["instance_id"],
                "INSTANCE_MISMATCH",
            )
            db.execute("BEGIN IMMEDIATE" if mode != "read" else "BEGIN")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def initialize(self, *, instance_id=None, bot_id=None):
        if self.root.exists() and any(self.root.iterdir()):
            with self.transaction("read"):
                pass
            return {"created": False, "instance_id": self.load()["instance_id"]}
        self.root.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".lurker-install-", dir=self.root.parent))
        try:
            stage.chmod(0o700)
            settings = copy.deepcopy(DEFAULTS)
            settings["instance_id"] = instance_id or ident()
            settings["host"]["bot_id"] = bot_id
            validate(settings)
            atomic_json(stage / "settings.json", settings)
            from .util import atomic_bytes

            atomic_bytes(stage / ".env", b"TIKHUB_API_KEY=\n")
            db = sqlite3.connect(stage / "state.sqlite")
            try:
                db.executescript(Path(__file__).with_name("schema.sql").read_text())
                db.execute("PRAGMA journal_mode=DELETE")
                now = int(self.clock())
                db.execute(
                    "INSERT INTO runtime(singleton,instance_id,request_count_day,updated_at) VALUES(1,?,?,?)",
                    (settings["instance_id"], utc_day(now), now),
                )
                db.commit()
            finally:
                db.close()
            (stage / "state.sqlite").chmod(0o600)
            if self.root.exists():
                self.root.rmdir()
            stage.rename(self.root)
            from .util import fsync_dir

            fsync_dir(self.root.parent)
            return {"created": True, "instance_id": settings["instance_id"]}
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def credentials(self):
        text = self.path(".env").read_text()
        require(len(text) < 16384, "ENV_INVALID")
        found = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            require("=" in line, "ENV_INVALID")
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            require(k == "TIKHUB_API_KEY" and k not in found, "ENV_INVALID")
            if v[:1] in {'"', "'"}:
                require(len(v) >= 2 and v[-1] == v[0], "ENV_INVALID")
                v = v[1:-1]
            require(not any(c.isspace() or ord(c) < 32 for c in v), "ENV_INVALID")
            found[k] = v
        require(
            bool(found.get("TIKHUB_API_KEY")), "CREDENTIALS_MISSING", "请在当前实例 .env 配置 TIKHUB_API_KEY"
        )
        return found["TIKHUB_API_KEY"]
