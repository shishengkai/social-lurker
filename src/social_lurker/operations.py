"""Local setup evidence, explicit exports and narrowly scoped instance maintenance."""

import copy
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import __version__
from .config import validate
from .errors import LurkerError, require
from .releases import AUTHORITY, package_bytes, prepare, verify_directory
from .schedule import context, next_slot
from .sources import ADAPTER_VERSION, TikHub
from .state import array
from .util import atomic_bytes, atomic_json, canonical, digest, lock, safe_path


def install(instance, *, source=None, descriptor=None, bot_id=None):
    """A development source is explicit; the normal installer supplies a verified release."""
    result = instance.initialize(bot_id=bot_id)
    entry_missing = not instance.path("run.py").exists()
    if bot_id is not None:
        require(instance.load()["host"]["bot_id"] == bot_id, "INSTANCE_MISMATCH")
    if source is not None:
        source = safe_path(source)
        raw = package_bytes(source)
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True
        ).stdout.strip()
        manifest = dict(
            authority=AUTHORITY,
            product="social-lurker-lightweight",
            version=__version__,
            channel="stable",
            source_commit=commit,
            protocol=1,
            schema_version=1,
            files={n: digest(v) for n, v in raw.items()},
        )
        destination = instance.path("app/" + __version__)
        if destination.exists():
            verify_directory(destination, manifest)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stage = Path(tempfile.mkdtemp(prefix=".package-", dir=destination.parent))
            try:
                for name, data in raw.items():
                    target = safe_path(stage / name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    atomic_bytes(target, data, 0o400)
                atomic_json(stage / "manifest.json", manifest)
                verify_directory(stage, manifest)
                stage.rename(destination)
                from .util import fsync_dir

                fsync_dir(destination.parent)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
    else:
        require(descriptor is not None, "RELEASE_REQUIRED")
        require(descriptor["manifest"]["version"] == __version__, "INSTALL_VERSION_MISMATCH")
        destination = prepare(instance, descriptor)
    entry = instance.path("run.py")
    if entry.exists():
        require(entry.read_bytes() == (destination / "run.py").read_bytes(), "LAUNCHER_INCOMPATIBLE")
    else:
        atomic_bytes(entry, (destination / "run.py").read_bytes(), 0o500)
    instance.path("logs").mkdir(exist_ok=True, mode=0o700)
    instance.path("backups").mkdir(exist_ok=True, mode=0o700)
    return {
        **result,
        "version": __version__,
        "entry": str(entry),
        "development_preview": source is not None,
        "star_event": "install_completed" if entry_missing else None,
        "setup": check(instance),
    }


def check(instance):
    status = instance_status(instance)
    if status.get("maintenance"):
        return status
    settings = instance.load()
    missing = []
    try:
        instance.credentials()
    except LurkerError as error:
        missing.append(error.code)
    host = settings["host"]
    e = (host["evidence_ref"] or {}).get("host", {})
    for key in ("durable_directory", "instance_isolated", "native_schedule_verified"):
        if not e.get(key):
            missing.append(key.upper() + "_UNVERIFIED")
    if not host["delivery_verified_at"]:
        missing.append("HOST_DELIVERY_UNVERIFIED")
    if not host["quiet_execution_verified_at"]:
        missing.append("HOST_QUIET_EXECUTION_UNVERIFIED")
    if not host["bot_id"]:
        missing.append("HOST_BOT_ID_REQUIRED")
    if not host["routine_id"]:
        missing.append("HOST_ROUTINE_REQUIRED")
    if not e.get("max_message_length"):
        missing.append("HOST_LENGTH_UNVERIFIED")
    for watch in status["watches"]:
        if (
            watch["status"] == "active"
            and TikHub(None).capability(settings, watch["platform"], watch["source_variant"]) == "unverified"
        ):
            missing.append("SOURCE_UNVERIFIED:" + watch["platform"] + ":" + watch["source_variant"])
    return {
        "instance_id": settings["instance_id"],
        "version": settings["app_version"],
        "mode": "automatic_ready" if not missing else "foreground_only",
        "missing": missing,
        "source_capabilities": {
            k: TikHub(None).capability(settings, *k.split(":"))
            for k in ("douyin:normal", "douyin:lite", "wechat_channels:default")
        },
        "routine": routine_plan(instance),
    }


def routine_snapshot(db, settings):
    return {
        "instance_id": settings["instance_id"],
        "routine_id": settings["host"]["routine_id"],
        "active": db.execute("SELECT count(*) FROM watches WHERE status='active'").fetchone()[0] > 0,
        "timezone": settings["timezone"],
        "monitor": settings["monitor"],
        "watch_generations": [
            (r["id"], r["generation"]) for r in db.execute("SELECT id,generation FROM watches ORDER BY id")
        ],
    }


def routine_plan(instance):
    with instance.transaction("read") as (db, settings):
        snapshot = routine_snapshot(db, settings)
    return {
        "routine_id": snapshot["routine_id"],
        "active": snapshot["active"],
        "timezone": settings["timezone"],
        "interval_seconds": settings["monitor"]["interval_seconds"],
        "anchor": settings["monitor"]["schedule_anchor"],
        "quiet_hours": settings["monitor"]["quiet_hours"],
        "binding_hash": digest(canonical(snapshot)),
        "notifications_per_activation": settings["limits"]["notifications_per_activation"],
        "entry": str(instance.path("run.py")),
        "instance": str(instance.root),
    }


def bind(instance, data):
    require(
        isinstance(data, dict) and set(data) <= {"bot_id", "routine_id", "host", "sources", "evidence"},
        "EVIDENCE_INVALID",
    )
    require(isinstance(data.get("evidence"), str) and 0 < len(data["evidence"]) <= 2048, "EVIDENCE_REQUIRED")
    now = int(instance.clock())
    # Evidence references point to real host results; never synthesize a verification timestamp from an assertion alone.
    with instance.transaction() as (db, settings):
        host = settings["host"]
        e = host["evidence_ref"] or {"schema": 1, "host": {}, "sources": {}}
        for key in ("bot_id", "routine_id"):
            if key in data:
                require(isinstance(data[key], str) and 0 < len(data[key]) <= 512, "HOST_BINDING_INVALID")
                if key == "bot_id" and host[key]:
                    require(host[key] == data[key], "INSTANCE_MISMATCH")
                host[key] = data[key]
        if "host" in data:
            h = data["host"]
            require(isinstance(h, dict), "EVIDENCE_INVALID")
            allowed = {
                "durable_directory",
                "instance_isolated",
                "native_schedule_verified",
                "quiet_execution_verified",
                "images_verified",
                "max_message_length",
                "length_unit",
                "delivery_update_id",
                "routine_plan_id",
                "routine_active",
                "routine_binding_hash",
            }
            require(set(h) <= allowed, "EVIDENCE_INVALID")
            for key in (
                "durable_directory",
                "instance_isolated",
                "native_schedule_verified",
                "quiet_execution_verified",
                "images_verified",
                "routine_active",
            ):
                if key in h:
                    require(type(h[key]) is bool, "EVIDENCE_INVALID")
            if "max_message_length" in h:
                require(
                    type(h["max_message_length"]) is int
                    and h["max_message_length"] > 0
                    and h.get("length_unit") in {"unicode", "utf8", "utf16"},
                    "EVIDENCE_INVALID",
                )
            if "delivery_update_id" in h:
                row = db.execute(
                    "SELECT state,reason,provider_message_id,sent_at FROM updates WHERE id=?",
                    (h["delivery_update_id"],),
                ).fetchone()
                require(
                    row and row["state"] == "sent" and row["reason"] == "test" and row["provider_message_id"],
                    "DELIVERY_PROOF_REQUIRED",
                )
                host["delivery_verified_at"] = row["sent_at"]
            if h.get("quiet_execution_verified"):
                host["quiet_execution_verified_at"] = now
            if "routine_active" in h or "routine_binding_hash" in h or h.get("native_schedule_verified"):
                snapshot = routine_snapshot(db, settings)
                require(
                    h.get("routine_binding_hash") == digest(canonical(snapshot))
                    and h.get("routine_active") is snapshot["active"],
                    "ROUTINE_PROOF_STALE",
                )
            e.setdefault("host", {}).update(h, evidence=data["evidence"], verified_at=now)
        if "sources" in data:
            require(isinstance(data["sources"], dict), "EVIDENCE_INVALID")
            for key, item in data["sources"].items():
                require(
                    key in {"douyin:normal", "douyin:lite", "wechat_channels:default"}
                    and isinstance(item, dict),
                    "EVIDENCE_INVALID",
                )
                require(
                    set(item) == {"adapter_version", "level", "evidence"}
                    and item["adapter_version"] == ADAPTER_VERSION,
                    "EVIDENCE_INVALID",
                )
                require(
                    item["level"]
                    in {"unverified", "recent_pages_verified", "enumeration_verified", "range_verified"}
                    and isinstance(item["evidence"], str)
                    and item["evidence"],
                    "EVIDENCE_INVALID",
                )
                e.setdefault("sources", {})[key] = item
        host["evidence_ref"] = e
        validate(settings)
        atomic_json(instance.path("settings.json"), settings)
    return check(instance)


def configure(instance, patch):
    require(
        isinstance(patch, dict) and set(patch) <= {"timezone", "monitor", "rate_limit", "limits"},
        "CONFIG_PATCH_INVALID",
    )
    with instance.transaction() as (db, settings):
        old = copy.deepcopy(settings)
        for key, value in patch.items():
            if isinstance(settings[key], dict):
                require(isinstance(value, dict), "CONFIG_PATCH_INVALID")
                settings[key].update(value)
            else:
                settings[key] = value
        validate(settings)
        now = int(instance.clock())
        if old["timezone"] != settings["timezone"] or old["monitor"] != settings["monitor"]:
            s, _ = context(now, settings)
            db.execute(
                "UPDATE runtime SET recovery_applied_slot=?,last_automatic_slot=NULL,updated_at=?", (s, now)
            )
            db.execute(
                "UPDATE watches SET next_check_at=?,scan_state_json=NULL,updated_at=? WHERE status='active'",
                (next_slot(now, settings), now),
            )
            # Changing a native schedule invalidates its old verification evidence.
            evidence = settings["host"]["evidence_ref"]
            if evidence:
                evidence.setdefault("host", {})["native_schedule_verified"] = False
        atomic_json(instance.path("settings.json"), settings)
    return {"configured": True, "routine": routine_plan(instance)}


def instance_status(instance):
    with lock(instance.root, "state.lock"):
        plan = instance.plan()
        if plan and not plan["business_writes_open"]:
            return {
                "maintenance": {
                    k: plan.get(k) for k in ("plan_id", "stage", "business_writes_open", "result")
                },
                "pending_receipts": sum(i["state"] == "pending" for i in plan["pending_receipts"]),
            }
    with instance.transaction("read") as (db, settings):
        rt = dict(db.execute("SELECT * FROM runtime").fetchone())
        notices = array(rt.pop("incidents_json"))
        rt["api_holds"] = array(rt.pop("api_holds_json"))
        rows = []
        for raw in db.execute("SELECT * FROM watches ORDER BY platform,author_id"):
            row = dict(raw)
            scan = row.pop("scan_state_json")
            row["scan_pending"] = scan is not None
            row["coverage_gaps"] = array(row.pop("coverage_gaps_json"))
            rows.append(row)
        return {
            "version": settings["app_version"],
            "runtime": rt,
            "watches": rows,
            "updates": {r[0]: r[1] for r in db.execute("SELECT state,count(*) FROM updates GROUP BY state")},
            "incidents": [{"id": n["id"], "code": n["code"], "state": n["state"]} for n in notices],
            "incident_capacity_reached": len(notices) >= 32,
            "maintenance": None if not plan else {"stage": plan["stage"], "plan_id": plan["plan_id"]},
        }


def export(instance, kind, path, *, watch_id=None, since=None, until=None):
    destination = safe_path(path)
    require(Path(path).is_absolute(), "EXPORT_PATH_INVALID")
    require(not destination.is_relative_to(instance.root), "EXPORT_PATH_INVALID", "导出写到实例目录之外")
    with instance.transaction("read") as (db, _):
        if kind == "watches":
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT platform,author_id,author_name,status,watch_since FROM watches ORDER BY platform,author_id"
                )
            ]
        else:
            require(
                kind == "updates" and (watch_id is not None or since is not None or until is not None),
                "EXPLICIT_RANGE_REQUIRED",
            )
            clauses = []
            params = []
            for column, op, value in (
                ("watch_id", "=", watch_id),
                ("published_at", ">=", since),
                ("published_at", "<=", until),
            ):
                if value is not None:
                    clauses.append(column + op + "?")
                    params.append(value)
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT platform,work_id,author_name,published_at,title,source_url,state FROM updates WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY published_at,work_id",
                    params,
                )
            ]
    raw = (canonical({"protocol": 1, "kind": kind, "items": rows}) + "\n").encode()
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    return {"path": str(destination), "count": len(rows)}


def uninstall(
    instance,
    *,
    confirmed=False,
    host_detached=False,
    purge=False,
    backup_path=None,
    discard_backup=False,
    abandon_unresolved=False,
):
    require(
        confirmed is True and host_detached is True,
        "UNINSTALL_AUTHORIZATION_REQUIRED",
        "须明确当前实例并核验宿主调度与技能绑定已停用",
    )
    require(not abandon_unresolved or purge, "UNRESOLVED_ABANDONMENT_REQUIRES_PURGE")

    def inspect(db):
        require(
            not db.execute("SELECT 1 FROM watches WHERE status='active'").fetchone(), "ACTIVE_WATCHES_REMAIN"
        )
        count = db.execute("SELECT count(*) FROM updates WHERE state IN ('sending','unknown')").fetchone()[0]
        count += sum(
            n["state"] in {"pending", "sending", "unknown"}
            for n in array(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
        )
        require(not count or abandon_unresolved is True, "UNRESOLVED_RECEIPTS")
        return count

    def fingerprints():
        return [
            (
                instance.path(n).stat().st_ino,
                instance.path(n).stat().st_size,
                instance.path(n).stat().st_mtime_ns,
            )
            for n in ("state.sqlite", "settings.json", ".env")
        ]

    stage = None
    tomb = None
    destination = None
    try:
        with lock(instance.root, "poll.lock", timeout=0), lock(instance.root, "request.lock", timeout=0):
            with instance.transaction() as (db, _):
                require(instance.plan() is None, "MAINTENANCE_ACTIVE")
                unresolved = inspect(db)
            with lock(instance.root, "state.lock"):
                before = fingerprints()
            if purge:
                require(backup_path is not None or discard_backup is True, "BACKUP_DECISION_REQUIRED")
                if backup_path:
                    destination = safe_path(backup_path)
                    require(
                        Path(backup_path).is_absolute()
                        and not destination.exists()
                        and not destination.is_relative_to(instance.root),
                        "BACKUP_PATH_INVALID",
                    )
                    require(not any(p.is_symlink() for p in instance.root.rglob("*")), "SYMLINK_REJECTED")
                    stage = Path(tempfile.mkdtemp(prefix=".lurker-backup-", dir=destination.parent))
                    shutil.copytree(
                        instance.root, stage, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock")
                    )
                    from .lifecycle import Lifecycle

                    Lifecycle(instance).inspect_db(stage / "state.sqlite", instance.load()["instance_id"])
            # Short final fence: a pause, receipt, configuration or key update during copying invalidates the snapshot.
            with lock(instance.root, "state.lock"):
                settings = instance.gate()
                with instance.connection(settings, "read") as db:
                    inspect(db)
                require(
                    fingerprints() == before, "INSTANCE_CHANGED", "实例在备份期间发生变化，请重新核对后执行"
                )
                if stage:
                    stage.rename(destination)
                    stage = None
                if purge:
                    from .util import ident

                    tomb = safe_path(instance.root.parent / (".removed-" + ident()))
                    instance.root.rename(tomb)
                else:
                    instance.path("run.py").unlink(missing_ok=True)
                    app = instance.path("app")
                    if app.exists():
                        from .util import ident

                        tomb = safe_path(instance.root.parent / (".removed-code-" + ident()))
                        app.rename(tomb)
        if tomb:
            shutil.rmtree(tomb)
        return (
            {
                "purged": True,
                "instance": str(instance.root),
                "backup": backup_path,
                "unresolved_discarded": unresolved,
            }
            if purge
            else {"uninstalled": True, "retained_data": str(instance.root)}
        )
    finally:
        if stage and stage.exists():
            shutil.rmtree(stage)
