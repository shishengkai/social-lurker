import pytest
from conftest import ingest, publication
from test_discovery import rows

from social_lurker.delivery import Delivery, format_payload
from social_lurker.errors import LurkerError
from social_lurker.store import Store
from social_lurker.util import atomic_json, canonical


def receipt(permit, clock, result="sent", **extra):
    value = {k: v for k, v in permit.items() if k != "payload"}
    value["result"] = result
    if result == "sent":
        value.update(provider_message_id="actual-fixture-message", sent_at=int(clock()))
    if result == "not_sent":
        value["evidence"] = {
            "type": "provider_rejected",
            "reference": "fixture-send-return",
            "attempt_id": permit["attempt_id"],
        }
    return {**value, **extra}


def test_one_work_one_immutable_permit_and_late_receipt(instance, watch, clock):
    ingest(instance, watch, [publication(watch, 2, clock.now), publication(watch, 1, clock.now - 1)])
    # Work 1 predates the watch, so only work 2 can receive a send permit.
    delivery = Delivery(instance)
    permit = delivery.next()
    assert permit["kind"] == "update" and "作者甲" in permit["payload"]["text"]
    assert len([r for r in rows(instance) if r["state"] == "sending"]) == 1
    Store(instance).transition(watch["id"], "paused")
    assert delivery.next() is None
    proof = receipt(permit, clock)
    assert delivery.report(proof)["state"] == "sent"
    assert delivery.report(proof)["duplicate"]
    assert next(r for r in rows(instance) if r["state"] == "sent")["payload"] == canonical(permit["payload"])


def test_no_receipt_unknown_not_resent_and_not_sent_evidence_required(instance, watch, clock):
    ingest(instance, watch, [publication(watch, 1, clock.now)])
    d = Delivery(instance)
    p = d.next()
    clock.advance(601)
    next_item = d.next()
    assert rows(instance)[0]["state"] == "unknown"
    assert next_item is None or next_item["kind"] == "incident"
    bad = receipt(p, clock, "not_sent")
    bad.pop("evidence")
    with pytest.raises(LurkerError, match="RECEIPT_EVIDENCE_REQUIRED"):
        d.report(bad)
    assert d.report(receipt(p, clock, "not_sent"))["state"] == "queued"
    clock.advance(31)
    second = d.next()
    assert second["attempt_id"] != p["attempt_id"]
    with pytest.raises(LurkerError, match="RECEIPT_ATTEMPT_MISMATCH"):
        d.report(receipt(p, clock))


def test_not_sent_after_pause_does_not_revive(instance, watch, clock):
    ingest(instance, watch, [publication(watch, 1, clock.now)])
    d = Delivery(instance)
    p = d.next()
    Store(instance).transition(watch["id"], "paused")
    assert d.report(receipt(p, clock, "not_sent"))["state"] == "cancelled"
    with pytest.raises(LurkerError, match="RECEIPT_CONFLICT"):
        d.report(receipt(p, clock))


def test_order_all_authors_images_and_length_no_split(instance, watch, clock):
    from social_lurker.sources import Author

    other = Store(instance).add(Author("douyin", "author-b", "乙", variant="normal"))
    clock.advance(10)
    ingest(
        instance,
        watch,
        [
            publication(watch, 3, clock.now, title="超长" * 200, cover_url="https://cdn.example.com/a.jpg"),
            publication(watch, 1, clock.now - 5),
        ],
    )
    ingest(instance, other, [publication(other, 2, clock.now - 2)])
    d = Delivery(instance)
    permits = [d.next() for _ in range(3)]
    assert [p["payload"]["text"].split("/")[-1] for p in permits] == ["1", "2", "3"]
    assert permits[-1]["payload"]["images"] == [{"url": "https://cdn.example.com/a.jpg", "alt": "作品封面"}]
    assert "…" in permits[-1]["payload"]["text"]
    settings = instance.load()
    settings["host"]["evidence_ref"]["host"]["max_message_length"] = 10
    with pytest.raises(LurkerError, match="MESSAGE_TOO_LONG"):
        format_payload(rows(instance)[0], settings)


def test_host_not_verified_only_explicit_test_and_proof_binding(instance, watch, clock):
    from social_lurker.operations import bind

    settings = instance.load()
    settings["host"]["delivery_verified_at"] = None
    atomic_json(instance.path("settings.json"), settings)
    ingest(instance, watch, [publication(watch, 1, clock.now)])
    with pytest.raises(LurkerError, match="HOST_DELIVERY_UNVERIFIED"):
        Delivery(instance).next()
    assert Delivery(instance).next(foreground_test=True) is None
    with instance.transaction() as (db, _):
        db.execute("UPDATE updates SET reason='test'")
    p = Delivery(instance).next(foreground_test=True)
    with pytest.raises(LurkerError, match="DELIVERY_PROOF_REQUIRED"):
        bind(instance, {"host": {"delivery_update_id": p["object_id"]}, "evidence": "fixture"})
    Delivery(instance).report(receipt(p, clock))
    bind(instance, {"host": {"delivery_update_id": p["object_id"]}, "evidence": "actual fixture receipt"})
    assert instance.load()["host"]["delivery_verified_at"] == clock.now


def test_freeze_journal_dedup_and_replay_after_commit_before_mark(instance, watch, clock, monkeypatch):
    import social_lurker.delivery as module

    ingest(instance, watch, [publication(watch, 1, clock.now)])
    d = Delivery(instance)
    p = d.next()
    proof = receipt(p, clock)
    plan = dict(
        protocol=1,
        instance_id=instance.load()["instance_id"],
        plan_id="test-plan",
        stage="frozen",
        business_writes_open=False,
        pending_receipts=[],
        source_version="0.3.0",
        target_version="0.3.1",
        source_sha="b" * 40,
        target_sha="a" * 40,
        schema_version=1,
        backup="backups/test-plan",
        old_settings=instance.load(),
    )
    atomic_json(instance.path("maintenance.json"), plan)
    assert d.report(proof)["state"] == "pending_registration"
    d.report(proof)
    assert len(instance.plan()["pending_receipts"]) == 1
    original = module.atomic_json

    def fail(*args):
        raise OSError("simulate power loss after DB commit")

    monkeypatch.setattr(module, "atomic_json", fail)
    from social_lurker.util import lock

    with lock(instance.root, "state.lock"):
        with pytest.raises(OSError):
            d.replay_locked(instance.plan(), instance.load())
    monkeypatch.setattr(module, "atomic_json", original)
    with lock(instance.root, "state.lock"):
        assert d.replay_locked(instance.plan(), instance.load())["pending_receipts"][0]["state"] == "applied"
    instance.path("maintenance.json").unlink()
    assert rows(instance)[0]["state"] == "sent"
