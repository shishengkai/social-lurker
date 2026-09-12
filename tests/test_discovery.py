import pytest
from conftest import Source, ingest, publication, timestamp

from social_lurker.errors import LurkerError
from social_lurker.monitor import Monitor
from social_lurker.schedule import context, next_slot
from social_lurker.sources import Author, Page
from social_lurker.store import Store
from social_lurker.util import parse_json


def rows(instance, table="updates"):
    with instance.transaction("read") as (db, _):
        return [dict(r) for r in db.execute("SELECT * FROM " + table)]


def test_schema_independence_and_no_initial_push(instance, watch, clock, tmp_path):
    from social_lurker.config import Instance

    other = Instance(str(tmp_path / "other"), clock=clock)
    other.initialize()
    assert rows(other, "watches") == []
    assert rows(instance) == []
    with instance.transaction("read") as (db, _):
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "runtime",
            "watches",
            "updates",
        }
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert watch["next_check_at"] == timestamp("09:00")


def test_first_success_one_page_and_failed_attempt_does_not_consume(instance, watch, clock):
    clock.now = timestamp("09:00")
    src = Source([LurkerError("HTTP_TEMPORARY", retryable=True)])
    Monitor(instance, src).poll()
    assert rows(instance, "watches")[0]["initial_check_pending"] == 1
    src = Source([Page([publication(watch, 1, clock.now - 1)], "next", "more")])
    result = Monitor(instance, src).poll()
    assert result["pages"] == 1 and len(src.calls) == 1
    assert rows(instance, "watches")[0]["initial_check_pending"] == 0
    assert rows(instance, "watches")[0]["scan_state_json"] is None


def test_conditional_pages_process_mixed_whole_page(instance, watch, clock):
    ingest(instance, watch, [])
    clock.advance(3600)
    src = Source(
        [
            Page([publication(watch, 1, clock.now - 1)], "page2", "more"),
            Page(
                [publication(watch, 2, clock.now - 2), publication(watch, 1, clock.now - 1)], "page3", "more"
            ),
        ]
    )
    result = Monitor(instance, src).poll()
    assert result["pages"] == 2 and [c[1] for c in src.calls] == [None, "page2"]
    assert {r["work_id"] for r in rows(instance)} == {"1", "2"}


def test_resume_cursor_without_homepage_and_stale_cursor(instance, watch, clock):
    ingest(instance, watch, [])
    clock.advance(3600)
    assert ingest(instance, watch, [publication(watch, 1, clock.now - 1)], cursor="page2")
    src = Source([Page([], None, "confirmed_end")])
    Monitor(instance, src).poll()
    assert src.calls[0][1] == "page2"
    clock.advance(1)
    assert ingest(instance, watch, [publication(watch, 2, clock.now)], cursor="again")
    clock.advance(86401)
    with instance.transaction() as (db, settings):
        w = dict(db.execute("SELECT * FROM watches").fetchone())
        scan = Store(instance).begin_scan(db, w, settings, clock.now)
        assert scan["cursor"] is None
        assert "cursor_expired" in db.execute("SELECT coverage_gaps_json FROM watches").fetchone()[0]


@pytest.mark.parametrize("bad", ["wrong_author", "loop", "duplicate_conflict"])
def test_page_invalid_is_atomic(instance, watch, clock, bad):
    ingest(instance, watch, [])
    clock.advance(60)
    assert ingest(instance, watch, [publication(watch, 1, clock.now)], cursor="same")
    with instance.transaction("read") as (db, _):
        scan = parse_json(db.execute("SELECT scan_state_json FROM watches").fetchone()[0])
    items = [publication(watch, 2, clock.now)]
    cursor = "new"
    if bad == "wrong_author":
        items.append(publication({**watch, "author_id": "other"}, 3, clock.now))
    if bad == "loop":
        cursor = "same"
    if bad == "duplicate_conflict":
        items.append(publication(watch, 2, clock.now, title="不同正文"))
    with pytest.raises(LurkerError):
        Store(instance).page(watch["id"], watch["generation"], scan, Page(items, cursor, "more"))
    assert [r["work_id"] for r in rows(instance)] == ["1"]
    assert parse_json(rows(instance, "watches")[0]["scan_state_json"]) == scan


def test_future_not_seen_and_missing_metadata_future_released(instance, watch, clock):
    ingest(
        instance,
        watch,
        [publication(watch, 1, clock.now + 100), publication(watch, 2, None, source_url=None)],
    )
    assert [r["work_id"] for r in rows(instance)] == ["2"]
    uid = rows(instance)[0]["id"]
    source = Source([publication(watch, 2, clock.now + 200)])
    assert Monitor(instance, source).metadata(uid)["state"] == "future"
    assert rows(instance) == []
    clock.advance(300)
    ingest(instance, watch, [publication(watch, 1, clock.now - 200), publication(watch, 2, clock.now - 100)])
    assert len(rows(instance)) == 2 and all(r["state"] == "queued" for r in rows(instance))


def test_metadata_terminal_and_original_window_survives_offline(instance, watch, clock):
    clock.advance(3600)
    ingest(instance, watch, [publication(watch, 1, clock.now - 1, source_url=None)])
    uid = rows(instance)[0]["id"]
    old_upper = rows(instance)[0]["eligibility_upper"]
    source = Source([publication(watch, 1, clock.now - 1, source_url=None)])
    Monitor(instance, source).metadata(uid)
    assert rows(instance)[0]["next_attempt_at"] is None
    clock.now = timestamp("15:00")
    with instance.transaction() as (db, settings):
        Store(instance).recover_and_claim(db, settings, clock.now, True)
    source = Source([publication(watch, 1, old_upper - 1)])
    Monitor(instance, source).metadata(uid)
    assert rows(instance)[0]["state"] == "queued" and rows(instance)[0]["eligibility_upper"] == old_upper


def test_pause_generation_cancels_pending_and_readd(instance, watch, clock):
    ingest(instance, watch, [publication(watch, 1, clock.now), publication(watch, 2, None)])
    oldgen = watch["generation"]
    store = Store(instance)
    paused = store.transition(watch["id"], "paused")
    assert paused["generation"] == oldgen + 1
    assert store.transition(watch["id"], "paused")["generation"] == paused["generation"]
    assert {r["state"] for r in rows(instance)} == {"cancelled"}
    clock.advance(60)
    resumed = store.transition(watch["id"], "active")
    assert resumed["watch_since"] == clock.now and resumed["initial_check_pending"] == 0
    store.transition(watch["id"], "removed")
    with pytest.raises(LurkerError, match="READD_REQUIRED"):
        store.transition(watch["id"], "active")
    assert (
        store.add(Author("douyin", watch["author_id"], "作者甲", variant="normal"))["initial_check_pending"]
        == 1
    )
    assert {r["state"] for r in rows(instance)} == {"cancelled"}


def test_d3_shared_anchor_manual_selected_author(instance, watch, clock):
    other = Store(instance).add(Author("douyin", "author-b", "乙", variant="normal"))
    clock.now = timestamp("09:00")
    Monitor(instance, Source([Page([], None, "confirmed_end"), Page([], None, "confirmed_end")])).poll(
        automatic=True
    )
    clock.now = timestamp("12:00")
    result = Monitor(instance, Source([Page([], None, "confirmed_end")])).poll(watch_id=watch["id"])
    assert result["recovered_watches"] == []
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT recovery_applied_slot FROM runtime").fetchone()[0] == timestamp("07:00")
    clock.now = timestamp("13:00")
    with instance.transaction() as (db, settings):
        _, changed = Store(instance).recover_and_claim(db, settings, clock.now, True)
        assert changed == [other["id"]]
        w = dict(db.execute("SELECT * FROM watches WHERE id=?", (watch["id"],)).fetchone())
        b = dict(db.execute("SELECT * FROM watches WHERE id=?", (other["id"],)).fetchone())
        assert w["discovery_floor"] == timestamp("08:00") and b["discovery_floor"] == clock.now


def test_day_slots_and_no_night_offline(instance, watch, clock):
    settings = instance.load()
    assert next_slot(timestamp("23:00"), settings) == timestamp("23:00") + 8 * 3600
    assert context(timestamp("23:00") + 8 * 3600, settings)[1] == timestamp("23:00")
    with instance.transaction() as (db, _):
        db.execute("UPDATE runtime SET last_automatic_slot=?", (timestamp("23:00"),))
    clock.now = timestamp("23:00") + 8 * 3600
    with instance.transaction() as (db, settings):
        assert Store(instance).recover_and_claim(db, settings, clock.now, True)[1] == []
    clock.now = timestamp("02:00")
    source = Source([])
    assert Monitor(instance, source).poll(automatic=True)["reason"] == "quiet_hours"
    assert not source.calls


def test_test_latest_reuses_ledger_only_revives_unattempted_history(instance, watch, clock):
    old = publication(watch, 1, clock.now - 100)
    ingest(instance, watch, [old])
    assert rows(instance)[0]["state"] == "ignored"
    monitor = Monitor(instance, Source([Page([old], None, "confirmed_end")] * 3))
    a = monitor.test_latest(watch["id"])
    b = monitor.test_latest(watch["id"])
    assert a["id"] == b["id"] and b["reason"] == "test" and b["state"] == "queued"
    Store(instance).transition(watch["id"], "paused")
    Store(instance).transition(watch["id"], "active")
    assert monitor.test_latest(watch["id"])["state"] == "cancelled"


def test_d3_recovery10_then_new1030_and_add12_no_inherited_gap(instance, watch, clock):
    with instance.transaction() as (db, _):
        db.execute(
            "UPDATE runtime SET last_automatic_slot=?,recovery_applied_slot=?",
            (timestamp("07:00") - 86400, timestamp("07:00") - 86400),
        )
        db.execute(
            "UPDATE watches SET watch_since=?,discovery_floor=?",
            (timestamp("08:00") - 86400, timestamp("08:00") - 86400),
        )
    clock.now = timestamp("10:00")
    Monitor(instance, Source([Page([], None, "confirmed_end")])).poll(watch_id=watch["id"])
    assert rows(instance, "watches")[0]["discovery_floor"] == clock.now
    clock.now = timestamp("11:00")
    Monitor(
        instance, Source([Page([publication(watch, 1, timestamp("10:30"))], None, "confirmed_end")])
    ).poll(automatic=True)
    assert rows(instance)[0]["state"] == "queued"
    clock.now = timestamp("12:00")
    other = Store(instance).add(Author("douyin", "added12", "乙", variant="normal"))
    with instance.transaction() as (db, _):
        db.execute(
            "UPDATE runtime SET last_automatic_slot=?,recovery_applied_slot=?",
            (timestamp("09:00"), timestamp("09:00")),
        )
    clock.now = timestamp("13:00")
    with instance.transaction() as (db, settings):
        changed = Store(instance).recover_and_claim(db, settings, clock.now, True)[1]
        assert other["id"] not in changed


def test_source_unverified_does_not_fake_attempt_or_failure(instance, watch, clock):
    source = Source([])
    source.capability = lambda *a: "unverified"
    clock.now = timestamp("09:00")
    result = Monitor(instance, source).poll(automatic=True)
    assert result["errors"][0]["code"] == "SOURCE_UNVERIFIED"
    current = rows(instance, "watches")[0]
    assert current["failure_count"] == 0 and current["last_attempt_at"] is None
    assert not source.calls


def test_pause_between_page_request_and_commit_rejects_entire_old_page(instance, watch, clock):
    store = Store(instance)
    with instance.transaction() as (db, settings):
        scan = store.begin_scan(db, watch, settings, clock.now)
    store.transition(watch["id"], "paused")
    clock.advance(5)
    store.transition(watch["id"], "active")
    with pytest.raises(LurkerError, match="WATCH_CHANGED"):
        store.page(
            watch["id"],
            watch["generation"],
            scan,
            Page([publication(watch, 1, clock.now - 5)], None, "confirmed_end"),
        )
    assert rows(instance) == []
