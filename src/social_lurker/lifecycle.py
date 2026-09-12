"""Persistent upgrade decision, frozen receipt inbox and explicit instance teardown."""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from .config import APPLICATION_ID, SCHEMA_VERSION
from .delivery import Delivery
from .errors import require
from .releases import prepare, validate_manifest, verify_directory
from .util import atomic_bytes, atomic_json, canonical, digest, ident, lock, parse_json, semver


class Lifecycle:
    def __init__(self, instance, *, checkpoint=lambda stage: None):
        self.instance = instance
        self.checkpoint = checkpoint

    def stage(self, value, **fields):
        with lock(self.instance.root, "state.lock"):
            plan = self.instance.plan()
            plan.update(stage=value, **fields)
            atomic_json(self.instance.path("maintenance.json"), plan)
        self.checkpoint(value)
        return plan

    def begin(self, descriptor, *, confirmed=False, read=None):
        require(confirmed is True, "UPGRADE_AUTHORIZATION_REQUIRED")
        with lock(self.instance.root, "poll.lock", timeout=0):
            with lock(self.instance.root, "state.lock"):
                old = self.instance.plan()
                if old:
                    return {"resume_required": True, "plan_id": old["plan_id"]}
                settings = self.instance.gate("read")
            manifest = validate_manifest(descriptor["manifest"])
            require(semver(manifest["version"]) > semver(settings["app_version"]), "UPGRADE_NOT_NEWER")
            target = prepare(self.instance, descriptor, **({"read": read} if read else {}))
            require(
                (target / "run.py").read_bytes() == self.instance.path("run.py").read_bytes(),
                "LAUNCHER_INCOMPATIBLE",
                "稳定入口变化需要单独受控处理",
            )
            with lock(self.instance.root, "state.lock"):
                require(self.instance.plan() is None, "MAINTENANCE_ACTIVE")
                current = self.instance.gate("read")
                require(current["app_version"] == settings["app_version"], "VERSION_CHANGED")
                plan = dict(
                    protocol=1,
                    plan_id=ident(),
                    instance_id=current["instance_id"],
                    source_version=current["app_version"],
                    target_version=manifest["version"],
                    target_sha=manifest["source_commit"],
                    source_sha=verify_directory(
                        self.instance.path("app/" + current["app_version"]),
                        parse_json(
                            self.instance.path(
                                "app/" + current["app_version"] + "/manifest.json"
                            ).read_bytes()
                        ),
                    )["source_commit"],
                    schema_version=SCHEMA_VERSION,
                    stage="prepared",
                    business_writes_open=False,
                    pending_receipts=[],
                    manifest=manifest,
                    backup=None,
                    backup_complete=False,
                    old_settings=None,
                    routine_state=None,
                    result=None,
                )
                atomic_json(self.instance.path("maintenance.json"), plan)
            self.checkpoint("prepared")
        return self.resume()

    def backup(self, plan):
        folder = self.instance.path(plan["backup"])
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = sqlite3.connect(self.instance.path("state.sqlite"))
        destination = sqlite3.connect(folder / "state.sqlite")
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        (folder / "state.sqlite").chmod(0o600)
        with (folder / "state.sqlite").open("rb") as stream:
            os.fsync(stream.fileno())
        atomic_json(folder / "settings.json", plan["old_settings"])
        files = {name: digest((folder / name).read_bytes()) for name in ("state.sqlite", "settings.json")}
        atomic_json(
            folder / "snapshot.json", {"complete": True, "files": files, "instance_id": plan["instance_id"]}
        )
        self.checkpoint("backup_written")
        with lock(self.instance.root, "state.lock"):
            current = self.instance.plan()
            current["backup_complete"] = True
            atomic_json(self.instance.path("maintenance.json"), current)

    def validate_backup(self, plan):
        require(
            plan.get("backup_complete") is True and isinstance(plan.get("backup"), str), "BACKUP_INCOMPLETE"
        )
        folder = self.instance.path(plan["backup"])
        require(folder.is_relative_to(self.instance.path("backups")), "BACKUP_INVALID")
        meta = parse_json((folder / "snapshot.json").read_bytes())
        require(meta["complete"] is True and meta["instance_id"] == plan["instance_id"], "BACKUP_INVALID")
        for name in ("state.sqlite", "settings.json"):
            require(digest((folder / name).read_bytes()) == meta["files"][name], "BACKUP_INVALID")
        return folder

    def inspect_db(self, path, instance_id):
        db = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
        try:
            require(
                db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID,
                "DATABASE_IDENTITY_INVALID",
            )
            require(db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, "SCHEMA_UNSUPPORTED")
            require(
                db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                and not db.execute("PRAGMA foreign_key_check").fetchall(),
                "DATABASE_INVALID",
            )
            require(
                db.execute("SELECT instance_id FROM runtime").fetchone()[0] == instance_id,
                "INSTANCE_MISMATCH",
            )
        finally:
            db.close()

    def rollback(self, plan):
        require(plan["stage"] not in {"committed", "done"}, "UPGRADE_ALREADY_COMMITTED")
        folder = self.validate_backup(plan)
        # The live maintenance journal is intentionally not part of this snapshot.
        atomic_bytes(self.instance.path("state.sqlite"), (folder / "state.sqlite").read_bytes())
        atomic_bytes(self.instance.path("settings.json"), (folder / "settings.json").read_bytes())
        self.inspect_db(self.instance.path("state.sqlite"), plan["instance_id"])
        return self.stage("rolled_back", result="rolled_back")

    def resume(self):
        with lock(self.instance.root, "poll.lock", timeout=0):
            with lock(self.instance.root, "state.lock"):
                plan = self.instance.plan()
                require(plan is not None, "NO_MAINTENANCE_PLAN")
            if plan["stage"] == "prepared":
                # All request producers observe prepared before the drain lock is released.
                with lock(self.instance.root, "request.lock", timeout=0):
                    with self.instance.transaction("result", maintenance=True) as (db, settings):
                        Delivery(self.instance).expire(db, int(self.instance.clock()))
                        sending = db.execute("SELECT count(*) FROM updates WHERE state='sending'").fetchone()[
                            0
                        ]
                        sending += sum(
                            i["state"] == "sending"
                            for i in parse_json(
                                db.execute("SELECT incidents_json FROM runtime").fetchone()[0]
                            )
                        )
                        if sending:
                            return {
                                "plan_id": plan["plan_id"],
                                "stage": "prepared",
                                "waiting_for_receipts": True,
                            }
                    with lock(self.instance.root, "state.lock"):
                        plan = self.instance.plan()
                        settings = self.instance.load()
                        plan.update(
                            stage="frozen",
                            old_settings=settings,
                            backup="backups/" + plan["plan_id"],
                            routine_state=settings["host"].copy(),
                        )
                        atomic_json(self.instance.path("maintenance.json"), plan)
                self.checkpoint("frozen")
            if plan["stage"] == "frozen":
                self.backup(plan)
                with lock(self.instance.root, "state.lock"):
                    plan = self.instance.plan()
                folder = self.validate_backup(plan)
                candidate = folder / "candidate.sqlite"
                shutil.copyfile(folder / "state.sqlite", candidate)
                candidate.chmod(0o600)
                target = self.instance.path("app/" + plan["target_version"])
                verify_directory(target, plan["manifest"])
                self.inspect_db(candidate, plan["instance_id"])
                # Validate the candidate with the selected target code, before changing either live file.
                command = [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import sys;sys.path.insert(0,sys.argv[1]);from social_lurker.lifecycle import candidate_check;candidate_check(sys.argv[2],sys.argv[3])",
                    str(target),
                    str(candidate),
                    plan["instance_id"],
                ]
                result = subprocess.run(command, capture_output=True, timeout=30)
                require(result.returncode == 0, "CANDIDATE_CHECK_FAILED")
                plan = self.stage("candidate_ready")
            elif plan["stage"] == "switching":
                plan = self.rollback(plan)
            if plan["stage"] == "candidate_ready":
                folder = self.validate_backup(plan)
                plan = self.stage("switching")
                try:
                    atomic_bytes(
                        self.instance.path("state.sqlite"), (folder / "candidate.sqlite").read_bytes()
                    )
                    self.checkpoint("database_switched")
                    new_settings = {**plan["old_settings"], "app_version": plan["target_version"]}
                    atomic_json(self.instance.path("settings.json"), new_settings)
                    self.checkpoint("binding_switched")
                    self.inspect_db(self.instance.path("state.sqlite"), plan["instance_id"])
                    plan = self.stage("committed", result="upgraded")
                except Exception:
                    with lock(self.instance.root, "state.lock"):
                        current = self.instance.plan()
                    if current["stage"] == "committed":
                        raise
                    plan = self.rollback(current)
            selected = (
                plan["target_version"]
                if plan["stage"] == "committed" or (plan["stage"] == "done" and plan["result"] == "upgraded")
                else plan["source_version"]
            )
            if plan["stage"] in {"committed", "rolled_back", "done"}:
                if self.instance.loaded_version != selected:
                    return {"reenter": True, "plan_id": plan["plan_id"], "stage": plan["stage"]}
                return self.finish()
            return {"plan_id": plan["plan_id"], "stage": plan["stage"]}

    def finish(self):
        with lock(self.instance.root, "state.lock"):
            plan = self.instance.plan()
            settings = self.instance.load()
            require(plan["stage"] in {"committed", "rolled_back", "done"}, "MAINTENANCE_STAGE_INVALID")
            require(settings["app_version"] == self.instance.loaded_version, "VERSION_CHANGED")
            if not plan["business_writes_open"]:
                plan = Delivery(self.instance).replay_locked(plan, settings)
                plan["business_writes_open"] = True
                atomic_json(self.instance.path("maintenance.json"), plan)
            # Native routine restoration is performed by the host, never guessed by Python.
            with self.instance.connection(settings, "read") as db:
                from .operations import activation_missing, routine_snapshot

                snapshot = routine_snapshot(db, settings)
                desired = snapshot["active"] and not activation_missing(
                    settings, db.execute("SELECT * FROM watches").fetchall()
                )
            original = plan["routine_state"] or {}
            if original.get("routine_id") is not None:
                host = (settings["host"].get("evidence_ref") or {}).get("host", {})
                if (
                    host.get("routine_plan_id") != plan["plan_id"]
                    or host.get("routine_active") is not desired
                    or host.get("routine_binding_hash") != digest(canonical(snapshot))
                ):
                    return {
                        "plan_id": plan["plan_id"],
                        "stage": plan["stage"],
                        "business_writes_open": True,
                        "routine_restore": {"routine_id": settings["host"]["routine_id"], "active": desired},
                        "result": plan["result"],
                    }
            plan["stage"] = "done"
            atomic_json(self.instance.path("maintenance.json"), plan)
            folder = self.instance.path(plan["backup"])
            require(folder.exists(), "BACKUP_INVALID")
            atomic_json(folder / "maintenance-result.json", plan)
            self.instance.path("maintenance.json").unlink()
            from .util import fsync_dir

            fsync_dir(self.instance.root)
            return {
                "plan_id": plan["plan_id"],
                "stage": "done",
                "result": plan["result"],
                "star_event": "upgrade_completed" if plan["result"] == "upgraded" else None,
            }


def candidate_check(path, instance_id):
    """Target-version self-check used by the stable maintenance coordinator."""
    from .config import Instance

    Lifecycle(Instance(str(Path(path).parent))).inspect_db(Path(path), instance_id)
