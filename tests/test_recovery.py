import json
import sqlite3
import subprocess

import pytest
from conftest import ASR, FakeMedia, Social, author, video
from test_workflows import complete_text, tick

from social_lurker.config import Instance
from social_lurker.control import Journal
from social_lurker.engine import Engine
from social_lurker.errors import LurkerError
from social_lurker.notifications import Notifications
from social_lurker.proofread import Proofreader
from social_lurker.providers.tikhub import Page
from social_lurker.store import Store
from social_lurker.util import file_lock, now, token


def test_two_bots_same_work_each_notifies_and_delete_isolated(env):
    instance, store, settings = env
    second = Instance(instance.root, token())
    other_settings = second.initialize(binding_confirmed=True)
    other_settings["execution"]["tick_soft_seconds"] = 1
    other = Store(second.db_path)
    try:
        for current in (env, (second, other, other_settings)):
            current[1].add_account(author(), "add")
            social, media, asr = Social(), FakeMedia(), ASR()
            tick(current, social, media, asr)
            tick(current, social, media, asr)
            complete_text(current)
        assert (
            len(store.all("SELECT * FROM notifications"))
            == len(other.all("SELECT * FROM notifications"))
            == 1
        )
        store.control(1, "delete")
        Engine(instance, store, settings, Social(), FakeMedia(), ASR()).cleanup()
        assert store.all("SELECT * FROM works") == []
        assert other.work(1)["processing_state"] == "ready"
    finally:
        other.close()


def test_three_works_two_authors_three_messages(env):
    instance, store, settings = env
    store.add_account(author(ident="a"), "a")
    store.add_account(author(ident="b"), "b")
    store.execute("UPDATE collection_runs SET kind='poll',scope_type='time',range_start=0")

    class Different(Social):
        def page(self, account, cursor):
            return Page(
                [video("1"), video("2")] if account["platform_account_id"] == "a" else [video("3")],
                None,
                False,
            )

    social, media, asr = Different(), FakeMedia(), ASR()
    for _ in range(3):
        tick(env, social, media, asr)
        tick(env, social, media, asr)
        pending = store.one("SELECT id FROM works WHERE processing_state='pending_proofread'")
        complete_text(env, pending["id"])
    assert asr.submits == 3
    assert len(store.all("SELECT * FROM notifications WHERE kind='work'")) == 3
    tick(env, social, media, asr)
    assert asr.submits == 3


def test_os_lock_beats_expired_lease(env):
    instance, store, settings = env
    with file_lock(instance.maintenance / "instance.lock"):
        code = "from social_lurker.util import file_lock; from pathlib import Path; import sys;\nwith file_lock(Path(sys.argv[1])): print('stolen')"
        import os

        result = subprocess.run(
            [os.sys.executable, "-c", code, str(instance.maintenance / "instance.lock")],
            capture_output=True,
            text=True,
            env=os.environ | {"PYTHONPATH": "src"},
        )
        assert result.returncode != 0 and "stolen" not in result.stdout


def test_proofread_expired_token_and_three_invalid_edits_preserve_raw(env):
    _, store, settings = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    proof = Proofreader(store, settings)
    segment = proof.next()
    store.execute("UPDATE works SET lease_until=0 WHERE id=1")
    fresh = proof.next()
    assert fresh["owner_token"] != segment["owner_token"]
    base = {"work_id": 1, "raw_hash": segment["raw_hash"], "start": 0, "end": len(asr.text), "edits": []}
    with pytest.raises(LurkerError):
        proof.submit(base | {"owner_token": segment["owner_token"]})
    for _ in range(3):
        with pytest.raises(LurkerError):
            proof.submit(
                base
                | {
                    "owner_token": fresh["owner_token"],
                    "edits": [{"start": 0, "end": 2, "old": asr.text[:2], "replacement": ""}],
                }
            )
    assert store.work(1)["processing_state"] == "blocked"
    assert store.work(1)["raw_transcript_text"] == asr.text
    assert asr.submits == 1


def test_crash_between_submit_intent_and_reply(env):
    instance, store, settings = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    store.execute("UPDATE works SET asr_phase='submitting',external_job_id=NULL WHERE id=1")
    tick(env, social, media, asr)
    assert store.work(1)["processing_state"] == "submit_unknown"
    assert asr.submits == 1


def test_stopped_running_history_finishes_scope_without_summary(env):
    from social_lurker.operations import confirm_collection, prepare_collection

    instance, store, settings = env
    store.add_account(author(), "add")
    store.execute("UPDATE collection_runs SET state='canceled'")
    plan = prepare_collection(store, 1, {"scope": "all"}, "history", settings["timezone"])
    social, media, asr = Social([Page([video("1"), video("2")], None, False)]), FakeMedia(), ASR()
    tick(env, social, media, asr)
    confirm_collection(store, plan["run_id"], {"acknowledged_count": 2, "accept_service_costs": True})
    tick(env, social, media, asr)
    store.control(1, "stop")
    tick(env, social, media, asr)
    complete_text(env, store.one("SELECT id FROM works WHERE processing_state='pending_proofread'")["id"])
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    complete_text(env, store.one("SELECT id FROM works WHERE processing_state='pending_proofread'")["id"])
    assert store.run(plan["run_id"])["state"] == "completed"
    assert store.all("SELECT * FROM notifications") == []


def test_no_secret_or_fulltext_in_control_journal(env):
    instance, store, _ = env
    request = {"request_id": "secret-update", "created_at": now()}
    journal = Journal(instance)

    def set_key(_):
        instance.set_credentials({"FAL_KEY": "very-private-value"})
        return {"updated_fields": ["FAL_KEY"]}

    journal.perform(request, "config:credentials", {"fields": ["FAL_KEY"]}, set_key)
    assert "very-private-value" not in journal.path.read_text()
    assert "very-private-value" not in "\n".join(store.conn.iterdump())


def test_notification_unknown_never_automatically_reclaimed(env):
    _, store, settings = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    complete_text(env)
    notifications = Notifications(store, settings)
    notifications.claim(1)
    store.execute("UPDATE notifications SET updated_at=?", (now() - 601,))
    tick(env, social, media, asr)
    assert store.one("SELECT state FROM notifications")["state"] == "unknown"
    with pytest.raises(LurkerError):
        notifications.claim(1)


def test_database_rejects_illegal_notification_shape(env):
    _, store, _ = env
    with pytest.raises(sqlite3.IntegrityError):
        store.execute(
            "INSERT INTO notifications(dedupe_key,kind,created_at,updated_at) VALUES('invalid','work',1,1)"
        )
    assert store.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert store.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_stage_retry_budget_is_per_stage_and_preserves_remote_job(env):
    _, store, settings = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    job = store.work(1)["external_job_id"]
    error = LurkerError("NETWORK_ERROR", "temporary", retryable=True, retry_after=999)
    store.fail_work(1, "query", error, settings)
    assert store.work(1)["next_attempt_at"] >= now() + 998
    store.fail_work(1, "query", error, settings)
    assert store.work(1)["processing_state"] == "retry_wait"
    store.fail_work(1, "query", error, settings)
    assert store.work(1)["processing_state"] == "failed"
    assert store.work(1)["external_job_id"] == job
    assert json.loads(store.work(1)["stage_attempts"]) == {"query": 3}


def test_control_intent_recovery_does_not_increment_epoch_twice(env):
    instance, store, _ = env
    store.add_account(author(), "add")
    journal = Journal(instance)
    request = {"request_id": "stop-once", "created_at": now()}

    def first(intent):
        store.control(1, "stop", intent["epoch"])
        raise OSError("crash before journal acknowledgement")

    with pytest.raises(OSError):
        journal.perform(request, "stop", {"account": 1}, first, lambda: {"epoch": 1})
    assert store.account(1)["watch_epoch"] == 2
    journal.perform(request, "stop", {"account": 1}, lambda intent: store.control(1, "stop", intent["epoch"]))
    assert store.account(1)["watch_epoch"] == 2


def test_deleting_inflight_waits_for_saved_text(env):
    instance, store, settings = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    store.control(1, "delete")
    tick(env, social, media, asr)
    assert store.account(1)["tracking_state"] == "deleting"
    complete_text(env)
    Engine(instance, store, settings, social, media, asr).cleanup()
    assert store.all("SELECT * FROM accounts") == []
    assert store.all("SELECT * FROM notifications") == []
