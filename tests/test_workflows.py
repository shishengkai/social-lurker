from dataclasses import replace

import pytest
from conftest import ASR, FakeMedia, Social, author, video

from social_lurker.engine import Engine
from social_lurker.errors import LurkerError
from social_lurker.notifications import Notifications
from social_lurker.operations import confirm_collection, prepare_collection, retry
from social_lurker.proofread import Proofreader
from social_lurker.providers.tikhub import Page
from social_lurker.util import now


def tick(env, social, media, asr, *, schedule=False):
    instance, store, settings = env
    store.execute("UPDATE works SET next_attempt_at=NULL")
    return Engine(instance, store, settings, social, media, asr).tick(schedule=schedule)


def complete_text(env, work_id=1, edits=None):
    _, store, settings = env
    proof = Proofreader(store, settings)
    while segment := proof.next(work_id):
        result = proof.submit(
            {
                "work_id": work_id,
                "owner_token": segment["owner_token"],
                "raw_hash": segment["raw_hash"],
                "start": segment["segment"]["start"],
                "end": segment["segment"]["end"],
                "edits": edits or [],
            }
        )
        if result["complete"]:
            break


def test_complete_pipeline_reuses_text_and_notifies_once(env):
    instance, store, settings = env
    added = store.add_account(author(), "add-1")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    assert asr.submits == 1
    tick(env, social, media, asr)
    work = store.work(1)
    assert work["raw_transcript_text"] == asr.text
    assert work["transcript_text"] is None
    assert not list((instance.path / "work").glob("*/*/audio.mp3"))
    complete_text(env)
    assert store.work(1)["transcript_text"] == asr.text
    notify = Notifications(store, settings)
    claim = notify.claim(1)
    rendered = notify.render(1, claim["dispatch_token"])
    assert rendered["body"].endswith(asr.text)
    notify.ack(
        1,
        claim["dispatch_token"],
        {
            "provider_message_id": "native-id-1",
            "body_hash": rendered["body_hash"],
            "readback_text": rendered["body"],
        },
    )
    tick(env, social, media, asr)
    assert asr.submits == 1
    assert store.one("SELECT count(*) n FROM notifications")["n"] == 1
    assert store.run(added["initial_run_id"])["state"] == "completed"


def test_stop_running_finishes_but_never_notifies_even_after_resume(env):
    _, store, _ = env
    added = store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    social.on_detail = lambda: store.control(added["account_id"], "stop")
    tick(env, social, media, asr)
    assert asr.submits == 1
    tick(env, social, media, asr)
    complete_text(env)
    assert store.work(1)["processing_state"] == "ready"
    assert store.all("SELECT * FROM notifications") == []
    old_epoch = store.account(1)["watch_epoch"]
    store.control(1, "resume")
    assert store.account(1)["watch_epoch"] == old_epoch + 1
    tick(env, social, media, asr, schedule=True)
    assert asr.submits == 1
    assert store.all("SELECT * FROM notifications") == []


def test_stop_queued_and_pending_notifications(env):
    _, store, settings = env
    store.add_account(author(), "add")
    store.control(1, "stop")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    assert social.page_calls == [] and asr.submits == 0
    assert store.run(1)["state"] == "canceled"


def test_submit_unknown_and_crash_intent_never_resubmit(env):
    _, store, _ = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    asr.submit_error = LurkerError("ASR_SUBMIT_UNKNOWN", "unknown")
    tick(env, social, media, asr)
    assert store.work(1)["processing_state"] == "submit_unknown"
    asr.submit_error = None
    tick(env, social, media, asr)
    assert asr.submits == 1
    with pytest.raises(LurkerError, match="提交不明"):
        retry(store, {"work_id": 1}, "retry")
    retry(store, {"work_id": 1, "external_job_id": "recovered"}, "retry")
    tick(env, social, media, asr)
    assert asr.submits == 1 and asr.queries == 1
    assert store.work(1)["processing_state"] == "pending_proofread"


def test_history_count_includes_initial_only_summary(env):
    _, store, settings = env
    store.add_account(author(), "add")
    social = Social([Page([video("old", now() - 1000), video("new", now() - 100)], None, False)])
    media, asr = FakeMedia(), ASR()
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    latest = store.one("SELECT id FROM works WHERE platform_work_id='new'")["id"]
    complete_text(env, latest)
    assert len(store.all("SELECT * FROM notifications WHERE kind='work'")) == 1
    plan = prepare_collection(store, 1, {"scope": "count", "count": 2}, "history", settings["timezone"])
    tick(env, social, media, asr)
    run = store.run(plan["run_id"])
    assert run["reported_total"] == 2 and asr.submits == 1
    confirm_collection(store, run["id"], {"acknowledged_count": 2, "accept_service_costs": True})
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    older = store.one("SELECT id FROM works WHERE platform_work_id='old'")["id"]
    complete_text(env, older)
    assert store.counts(run["id"]) == {"acquired": 1, "reused": 1}
    assert len(store.all("SELECT * FROM notifications WHERE kind='work'")) == 1
    assert len(store.all("SELECT * FROM notifications WHERE kind='history_summary'")) == 1


def test_all_does_not_start_asr_until_actual_count_accepted(env):
    _, store, settings = env
    store.add_account(author(), "add")
    store.execute("UPDATE collection_runs SET state='canceled'")
    plan = prepare_collection(store, 1, {"scope": "all"}, "history", settings["timezone"])
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    assert asr.submits == 0
    with pytest.raises(LurkerError):
        confirm_collection(store, plan["run_id"], {"acknowledged_count": 999, "accept_service_costs": True})
    confirm_collection(store, plan["run_id"], {"acknowledged_count": 1, "accept_service_costs": True})
    tick(env, social, media, asr)
    assert asr.submits == 1


def test_pagination_budget_does_not_advance_coverage_or_lose_cursor(env):
    _, store, settings = env
    settings["execution"]["max_pages_per_tick"] = 1
    store.add_account(author(), "add")
    social = Social([Page([video("old", now() - 1000)], "1", None), Page([video("new")], None, False)])
    media, asr = FakeMedia(), ASR()
    tick(env, social, media, asr)
    assert store.account(1)["coverage_until"] is None and asr.submits == 0
    tick(env, social, media, asr)
    assert social.page_calls == [None, "1"]
    assert store.account(1)["coverage_until"] is not None
    assert (
        store.one("SELECT platform_work_id FROM works WHERE external_job_id IS NOT NULL")["platform_work_id"]
        == "new"
    )


def test_cursor_cycle_is_not_success_and_no_asr(env):
    _, store, _ = env
    store.add_account(author("wechat_channels"), "add")
    social = Social([Page([video()], "1", None), Page([video()], "1", None)])
    asr = ASR()
    tick(env, social, FakeMedia(), asr)
    assert store.run(1)["last_error_code"] == "ENUMERATION_UNCERTAIN"
    assert not store.run(1)["enumeration_complete"]
    assert asr.submits == 0


def test_missing_publication_does_not_become_new(env):
    _, store, _ = env
    store.add_account(author(), "add")
    social = Social([Page([replace(video(), published_at=None)], None, False)])
    asr = ASR()
    tick(env, social, FakeMedia(), asr)
    assert store.work(1)["published_at"] is None
    assert store.run(1)["last_error_code"] == "PUBLISHED_AT_UNKNOWN"
    assert store.account(1)["coverage_until"] is None and asr.submits == 0


def test_no_speech_is_explicit_and_not_proofread(env):
    _, store, _ = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR("")
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    assert store.work(1)["processing_state"] == "no_speech"
    assert store.work(1)["transcript_text"] is None
    assert store.one("SELECT kind FROM notifications")["kind"] == "work"


def test_long_notification_never_truncates_splits_or_writes_file(env):
    instance, store, settings = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR("甲" * 17000 + "尾部")
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    complete_text(env)
    settings["delivery"]["max_chars"] = 1000
    result = Notifications(store, settings).claim(1)
    assert result["blocked"] and result["error_code"] == "CONTENT_TOO_LONG"
    assert store.work(1)["transcript_text"] == asr.text
    assert len(store.all("SELECT * FROM notifications WHERE kind='work'")) == 1
    assert len(store.all("SELECT * FROM notifications WHERE kind='incident'")) == 1
    assert not list(instance.path.rglob("*.txt"))


def test_sending_unknown_stop_and_readback_verification(env):
    _, store, settings = env
    store.add_account(author(), "add")
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    complete_text(env)
    notify = Notifications(store, settings)
    claim = notify.claim(1)
    body = notify.render(1, claim["dispatch_token"])
    with pytest.raises(LurkerError):
        notify.ack(
            1,
            claim["dispatch_token"],
            {"provider_message_id": "m", "body_hash": body["body_hash"], "readback_text": "截短"},
        )
    store.control(1, "stop")
    assert store.one("SELECT state FROM notifications")["state"] == "unknown"
    with pytest.raises(LurkerError):
        notify.render(1, claim["dispatch_token"])
    notify.ack(1, claim["dispatch_token"], {"provider_message_id": "m", "body_hash": body["body_hash"]})
    assert store.one("SELECT state FROM notifications")["state"] == "sent"


def test_failed_history_retry_keeps_old_summary(env):
    _, store, settings = env
    store.add_account(author(), "add")
    store.execute("UPDATE collection_runs SET state='canceled'")
    plan = prepare_collection(store, 1, {"scope": "count", "count": 1}, "h1", settings["timezone"])
    social, media, asr = Social(), FakeMedia(), ASR()
    tick(env, social, media, asr)
    confirm_collection(store, plan["run_id"], {"acknowledged_count": 1, "accept_service_costs": True})
    store.execute("UPDATE works SET processing_state='failed'")
    store.execute("UPDATE collection_items SET result='failed' WHERE run_id=?", (plan["run_id"],))
    store.finish_runs()
    child = retry(store, {"run_id": plan["run_id"], "work_ids": [1]}, "h2")
    tick(env, social, media, asr)
    tick(env, social, media, asr)
    complete_text(env)
    assert store.counts(plan["run_id"]) == {"failed": 1}
    assert store.counts(child["run_id"]) == {"acquired": 1}
