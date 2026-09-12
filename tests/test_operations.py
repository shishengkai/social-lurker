import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ingest, publication, timestamp

from social_lurker.config import Instance
from social_lurker.errors import LurkerError
from social_lurker.operations import bind, configure, export, instance_status, routine_plan, uninstall
from social_lurker.state import incident
from social_lurker.store import Store
from social_lurker.util import atomic_json, lock

ROOT = Path(__file__).resolve().parents[1]


def test_config_rejects_invalid_before_write_and_rebases_valid_schedule(instance, watch, clock):
    old = instance.path("settings.json").read_bytes()
    with pytest.raises(LurkerError):
        configure(instance, {"monitor": {"interval_seconds": 5}})
    assert instance.path("settings.json").read_bytes() == old
    clock.now = timestamp("12:00")
    configure(instance, {"monitor": {"interval_seconds": 3600}})
    with instance.transaction("read") as (db, _):
        row = db.execute("SELECT * FROM watches").fetchone()
        assert row["discovery_floor"] == timestamp("08:00") and row["next_check_at"] == timestamp("13:00")
        assert db.execute("SELECT recovery_applied_slot FROM runtime").fetchone()[0] == timestamp("12:00")
    assert not instance.load()["host"]["evidence_ref"]["host"]["native_schedule_verified"]


def test_routine_ack_rejects_stale_pause_snapshot(instance, watch):
    plan = routine_plan(instance)
    Store(instance).transition(watch["id"], "paused")
    proof = {
        "evidence": "fixture-native-result",
        "host": {"routine_active": True, "routine_binding_hash": plan["binding_hash"]},
    }
    with pytest.raises(LurkerError, match="ROUTINE_PROOF_STALE"):
        bind(instance, proof)
    plan = routine_plan(instance)
    proof["host"].update(routine_active=False, routine_binding_hash=plan["binding_hash"])
    bind(instance, proof)
    assert instance.load()["host"]["evidence_ref"]["host"]["routine_active"] is False


def test_export_safe_new_file_only_and_no_private_internal_fields(instance, watch, clock, tmp_path):
    ingest(instance, watch, [publication(watch, 1, clock.now)])
    path = tmp_path / "export.json"
    result = export(instance, "updates", str(path), watch_id=watch["id"])
    assert result["count"] == 1 and path.stat().st_mode & 0o777 == 0o600
    text = path.read_text()
    assert not any(k in text for k in ("fixture-only", "attempt_id", "payload", "cursor"))
    with pytest.raises(FileExistsError):
        export(instance, "updates", str(path), watch_id=watch["id"])
    with pytest.raises(LurkerError):
        export(instance, "updates", str(tmp_path / "all.json"))
    with pytest.raises(LurkerError):
        export(instance, "watches", str(instance.path(".env")))


def test_no_autocreate_no_symlinks_and_instance_binding(instance, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(instance.root, target_is_directory=True)
    with pytest.raises(LurkerError, match="SYMLINK_REJECTED"):
        Instance(str(alias))
    with pytest.raises(LurkerError, match="UNSAFE_PATH"):
        instance.path("../outside")
    instance.path("state.sqlite").unlink()
    with pytest.raises(LurkerError, match="DATABASE_MISSING"):
        with instance.transaction():
            pass
    assert not instance.path("state.sqlite").exists()


def test_lock_order_and_separate_process_exclusion(instance):
    with lock(instance.root, "state.lock"):
        with pytest.raises(LurkerError, match="LOCK_ORDER_INVALID"):
            with lock(instance.root, "request.lock"):
                pass
        code = 'import fcntl,sys;f=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
        result = subprocess.run(
            [sys.executable, "-c", code, str(instance.path("state.lock"))], capture_output=True
        )
        assert result.returncode != 0


def test_malformed_maintenance_blocks_read_write_without_db_open(instance, watch):
    atomic_json(
        instance.path("maintenance.json"),
        {
            "protocol": 1,
            "stage": "committed",
            "business_writes_open": True,
            "pending_receipts": [],
            "instance_id": instance.load()["instance_id"],
            "plan_id": "forged",
        },
    )
    with pytest.raises(LurkerError, match="MAINTENANCE_INVALID"):
        Store(instance).transition(watch["id"], "paused")
    with pytest.raises(LurkerError, match="MAINTENANCE_INVALID"):
        instance_status(instance)


def test_source_channel_change_keeps_ledger_and_clears_scan(instance, watch, clock):
    ingest(instance, watch, [])
    clock.advance(60)
    ingest(instance, watch, [publication(watch, 1, clock.now)], cursor="a")
    result = Store(instance).change_variant(watch["id"], "lite")
    assert result["scan_state_json"] is None and result["generation"] == watch["generation"]
    assert instance_status(instance)["updates"] == {"queued": 1}


def test_incident_capacity_keeps_recent_delivery_and_unknown(instance, clock):
    with instance.transaction() as (db, _):
        for i in range(32):
            incident(db, "ERROR", str(i), clock.now)
        notices = json.loads(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
        notices[0].update(state="sent", last_sent_at=clock.now)
        notices[1].update(state="unknown")
        db.execute("UPDATE runtime SET incidents_json=?", (json.dumps(notices),))
        incident(db, "NEW_ERROR", "one", clock.now + 1)
        assert json.loads(db.execute("SELECT incidents_json FROM runtime").fetchone()[0]) == notices
    assert instance_status(instance)["incident_capacity_reached"]


def test_cli_errors_never_echo_key_unknown_params_or_traceback(instance):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/dev.py"),
            "--instance",
            str(instance.root),
            "--json",
            '{"protocol":1,"watch_id":"pretend-secret-value"}',
            "watch",
            "pause",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "WATCH_NOT_FOUND"
    assert "pretend-secret-value" not in result.stdout + result.stderr and "Traceback" not in result.stderr
    log = instance.path("logs/lurker.log").read_text()
    assert "WATCH_NOT_FOUND" in log and "pretend-secret-value" not in log


def test_uninstall_requires_explicit_object_and_preserves_data(instance, watch):
    with pytest.raises(LurkerError):
        uninstall(instance, confirmed=True, host_detached=True)
    Store(instance).transition(watch["id"], "paused")
    result = uninstall(instance, confirmed=True, host_detached=True)
    assert result["retained_data"] == str(instance.root)
    assert instance.path("state.sqlite").exists() and instance.path(".env").exists()


def test_logs_rotate_bounded_and_ignore_free_text(instance):
    from social_lurker.logs import record

    record(instance, "secret=must-not-log", maximum=10)
    for _ in range(20):
        record(instance, "HTTP_TEMPORARY", maximum=10)
    assert {p.name for p in instance.path("logs").iterdir()} == {"lurker.log", "lurker.log.1", "lurker.log.2"}
    assert all(p.stat().st_size < 200 for p in instance.path("logs").iterdir())


@pytest.mark.parametrize(
    "data",
    [
        {"protocol": 1, "automatic": "false"},
        {"protocol": 1, "unknown_key": True},
        {"protocol": False},
        {"protocol": 2},
    ],
)
def test_cli_rejects_unknown_or_ambiguous_parameters_before_action(instance, data):
    from social_lurker.cli import execute

    with pytest.raises(LurkerError):
        execute(instance, ("poll",), data)


def test_purge_explicit_backup_and_unresolved_abandonment(instance, watch, clock, tmp_path):
    from social_lurker.delivery import Delivery

    ingest(instance, watch, [publication(watch, 1, clock.now)])
    Delivery(instance).next()
    Store(instance).transition(watch["id"], "paused")
    with pytest.raises(LurkerError, match="UNRESOLVED_RECEIPTS"):
        uninstall(instance, confirmed=True, host_detached=True, purge=True, discard_backup=True)
    backup = tmp_path / "saved-copy"
    result = uninstall(
        instance,
        confirmed=True,
        host_detached=True,
        purge=True,
        backup_path=str(backup),
        abandon_unresolved=True,
    )
    assert result["unresolved_discarded"] == 1 and not instance.root.exists()
    assert (backup / "state.sqlite").exists() and (backup / ".env").stat().st_mode & 0o777 == 0o600
