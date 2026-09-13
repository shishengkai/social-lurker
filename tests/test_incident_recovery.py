import json

import pytest
from conftest import Source, ingest, publication, timestamp
from test_delivery import receipt

from social_lurker.delivery import Delivery
from social_lurker.errors import LurkerError
from social_lurker.monitor import Monitor
from social_lurker.sources import Author, Page
from social_lurker.state import add_hold, incident
from social_lurker.store import Store
from social_lurker.util import canonical


def notices(instance):
    with instance.transaction("read") as (db, _):
        return json.loads(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])


def fail_watch(instance, watch):
    Store(instance).failure(watch["id"], watch["generation"], LurkerError("PAGE_IDENTITY_INVALID"))


def test_successful_automatic_round_does_not_deliver_recovered_foreground_fault(instance, watch, clock):
    failed = Monitor(instance, Source([LurkerError("PAGE_IDENTITY_INVALID")])).poll()
    assert failed["errors"] and notices(instance)[0]["state"] == "pending"
    clock.now = timestamp("09:00")
    result = Monitor(instance, Source([Page([], None, "confirmed_end")])).poll(automatic=True)
    assert result["pages"] == 1 and result["errors"] == []
    assert Delivery(instance).next(automatic=True) is None
    assert notices(instance)[0]["state"] == "resolved"


def test_permit_reconciles_legacy_pending_against_existing_success_without_network(instance, watch, clock):
    fail_watch(instance, watch)
    legacy = notices(instance)
    clock.advance(10)
    ingest(instance, watch, [])
    # Older versions left this exact pending snapshot after committing the successful scan.
    with instance.transaction() as (db, _):
        db.execute("UPDATE runtime SET incidents_json=?", (canonical(legacy),))
    assert Delivery(instance).next() is None
    saved = notices(instance)[0]
    assert saved["id"] == legacy[0]["id"] and saved["resolved_at"] == clock.now


def test_same_fault_for_another_watch_and_api_hold_are_not_resolved(instance, watch, clock):
    other = Store(instance).add(Author("douyin", "other-author", "另一作者", variant="normal"))
    fail_watch(instance, watch)
    fail_watch(instance, other)
    endpoint = "/douyin/app/v3/fetch_user_post_videos"
    with instance.transaction() as (db, _):
        add_hold(db, endpoint, "API_PERMISSION", clock.now)
    before = notices(instance)
    clock.advance(10)
    ingest(instance, watch, [])
    after = notices(instance)
    assert after[0]["state"] == "resolved"
    assert after[1:] == before[1:]
    permit = Delivery(instance).next()
    assert permit["object_id"] == before[1]["id"]
    with instance.transaction("read") as (db, _):
        assert len(json.loads(db.execute("SELECT api_holds_json FROM runtime").fetchone()[0])) == 1


def test_unfinished_scan_does_not_certify_recovery(instance, watch, clock):
    ingest(instance, watch, [])
    clock.advance(10)
    fail_watch(instance, watch)
    clock.advance(10)
    assert ingest(instance, watch, [publication(watch, 1, clock.now)], cursor="next")
    saved = notices(instance)[0]
    assert saved["state"] == "pending" and saved.get("resolved_at") is None
    assert not ingest(instance, watch, [])
    assert notices(instance)[0]["state"] == "resolved"


@pytest.mark.parametrize("becomes_unknown", [False, True])
def test_recovery_preserves_inflight_payload_and_accepts_late_sent_receipt(
    instance, watch, clock, becomes_unknown
):
    fail_watch(instance, watch)
    delivery = Delivery(instance)
    permit = delivery.next()
    if becomes_unknown:
        delivery.report(receipt(permit, clock, "unknown"))
    before = notices(instance)[0]
    clock.advance(10)
    ingest(instance, watch, [])
    after = notices(instance)[0]
    assert {k: after[k] for k in before if k != "resolved_at"} == {
        k: v for k, v in before.items() if k != "resolved_at"
    }
    assert after["resolved_at"] == clock.now
    assert delivery.next() is None
    proof = receipt(permit, clock)
    assert delivery.report(proof)["state"] == "sent"
    assert delivery.report(proof)["duplicate"]
    assert notices(instance)[0]["resolved_at"] == clock.now


def test_late_not_sent_of_recovered_incident_does_not_requeue(instance, watch, clock):
    fail_watch(instance, watch)
    delivery = Delivery(instance)
    permit = delivery.next()
    clock.advance(10)
    ingest(instance, watch, [])
    proof = receipt(permit, clock, "not_sent")
    assert delivery.report(proof)["state"] == "resolved"
    assert delivery.report(proof)["duplicate"]
    assert delivery.next() is None


def test_new_episode_does_not_overwrite_resolved_unknown_receipt(instance, watch, clock):
    fail_watch(instance, watch)
    delivery = Delivery(instance)
    old = delivery.next()
    delivery.report(receipt(old, clock, "unknown"))
    clock.advance(10)
    ingest(instance, watch, [])
    prior = notices(instance)[0]
    clock.advance(10)
    fail_watch(instance, watch)
    fresh = delivery.next()
    assert fresh["object_id"] != old["object_id"]
    assert notices(instance)[0] == prior
    assert delivery.report(receipt(old, clock))["state"] == "sent"
    assert notices(instance)[1]["state"] == "sending"


def test_sent_fault_cooldown_and_new_episode_preserve_old_receipt(instance, watch, clock):
    fail_watch(instance, watch)
    delivery = Delivery(instance)
    old = delivery.next()
    proof = receipt(old, clock)
    delivery.report(proof)
    clock.advance(10)
    fail_watch(instance, watch)
    assert delivery.next() is None
    ingest(instance, watch, [])
    clock.advance(10)
    fail_watch(instance, watch)
    fresh = delivery.next()
    assert fresh["object_id"] != old["object_id"]
    assert delivery.report(proof) == {"state": "sent", "duplicate": True}


def test_unrecovered_fault_after_cooldown_keeps_previous_attempt(instance, watch, clock):
    fail_watch(instance, watch)
    delivery = Delivery(instance)
    old = delivery.next()
    proof = receipt(old, clock)
    delivery.report(proof)
    clock.advance(86401)
    fail_watch(instance, watch)
    fresh = delivery.next()
    assert fresh["object_id"] != old["object_id"]
    assert delivery.report(proof) == {"state": "sent", "duplicate": True}


def test_resolved_capacity_keeps_recent_and_unknown_snapshots(instance, watch, clock):
    fail_watch(instance, watch)
    clock.advance(10)
    ingest(instance, watch, [])
    recovered = notices(instance)[0]
    rows = [{**recovered, "id": f"old-{i}", "key": f"old-key-{i}"} for i in range(31)]
    unresolved = {**recovered, "id": "unknown", "key": "unknown-key", "state": "unknown"}
    rows.append(unresolved)
    with instance.transaction() as (db, _):
        db.execute("UPDATE runtime SET incidents_json=?", (canonical(rows),))
        incident(db, "PAGE_IDENTITY_INVALID", "new-watch", clock.now)
    assert notices(instance) == rows
    clock.advance(86401)
    with instance.transaction() as (db, _):
        incident(db, "PAGE_IDENTITY_INVALID", "new-watch", clock.now)
    after = notices(instance)
    assert len(after) == 32
    assert unresolved in after
    assert after[-1]["state"] == "pending"
