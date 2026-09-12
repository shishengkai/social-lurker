import threading

import pytest
from conftest import timestamp

from social_lurker.errors import LurkerError
from social_lurker.http import Client, Response
from social_lurker.sources import DY, WX, TikHub
from social_lurker.state import add_hold, holds
from social_lurker.store import Store
from social_lurker.util import atomic_json

ENDPOINT = DY + "handler_user_profile"
PARAMS = {"sec_user_id": "author-a"}
SUCCESS = Response(200, {"code": 200, "data": {"user": {"sec_uid": "author-a", "nickname": "甲"}}})


def client(instance, clock, send):
    return Client(instance, send=send, sleep=clock.sleep, monotonic=clock.mono)


def test_combined_rps_persists_across_clients_and_endpoint_override(instance, clock):
    starts = []

    def send(*a):
        return starts.append(clock.now) or SUCCESS

    client(instance, clock, send).call(ENDPOINT, PARAMS)
    client(instance, clock, send).call(ENDPOINT, PARAMS)
    assert starts[1] - starts[0] >= 1
    settings = instance.load()
    settings["rate_limit"]["endpoint_overrides"][ENDPOINT] = 0.25
    atomic_json(instance.path("settings.json"), settings)
    client(instance, clock, send).call(ENDPOINT, PARAMS)
    client(instance, clock, send).call(ENDPOINT, PARAMS)
    assert starts[-1] - starts[-2] >= 4
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT requests_reserved FROM runtime").fetchone()[0] == 4


def test_429_survives_key_change_and_verify_does_not_bypass(instance, clock):
    calls = []
    c = client(instance, clock, lambda *a: calls.append(1) or Response(429, None, {"retry-after": "100"}))
    with pytest.raises(LurkerError, match="API_RATE_WAIT"):
        c.call(ENDPOINT, PARAMS)
    with instance.transaction() as (db, _):
        add_hold(db, ENDPOINT, "API_AUTH", clock.now)
    instance.path(".env").write_text("TIKHUB_API_KEY=another-fixture\n")
    with instance.transaction("read") as (db, _):
        hold_id = holds(db)[0]["hold_id"]
    with pytest.raises(LurkerError, match="API_RATE_WAIT"):
        client(instance, clock, lambda *a: SUCCESS).verify(hold_id, PARAMS)
    assert len(calls) == 1
    clock.advance(101)
    assert client(instance, clock, lambda *a: SUCCESS).verify(hold_id, PARAMS)["notification_sent"] is False


def test_hold_chain_same_path_exempts_applicable_only_after_real_success(instance, clock):
    with instance.transaction() as (db, _):
        add_hold(db, ENDPOINT, "API_PERMISSION", clock.now)
        add_hold(db, WX + "fetch_user_profile", "API_PERMISSION", clock.now)
        h1 = next(h["hold_id"] for h in holds(db) if h["endpoint"] == ENDPOINT)
    with pytest.raises(LurkerError, match="API_AUTH"):
        client(instance, clock, lambda *a: Response(401, {})).verify(h1, PARAMS)
    with instance.transaction("read") as (db, _):
        assert len(holds(db)) == 3
    with pytest.raises(LurkerError, match="API_HELD"):
        client(instance, clock, lambda *a: SUCCESS).call(ENDPOINT, PARAMS)
    with pytest.raises(LurkerError, match="PAGE_IDENTITY_INVALID"):
        client(instance, clock, lambda *a: Response(200, {"code": 200, "data": {}})).verify(h1, PARAMS)
    with instance.transaction("read") as (db, _):
        assert len(holds(db)) == 3
    client(instance, clock, lambda *a: SUCCESS).verify(h1, PARAMS)
    with instance.transaction("read") as (db, _):
        assert [h["endpoint"] for h in holds(db)] == [WX + "fetch_user_profile"]


def test_pause_does_not_wait_network_and_following_request_rechecks_generation(instance, watch, clock):
    started = threading.Event()
    release = threading.Event()
    out = []

    def send(*args):
        started.set()
        assert release.wait(5)
        return SUCCESS

    def run():
        try:
            out.append(
                Client(instance, send=send).call(ENDPOINT, PARAMS, watch=(watch["id"], watch["generation"]))
            )
        except Exception as error:
            out.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    paused = Store(instance).transition(watch["id"], "paused")
    assert paused["status"] == "paused" and not release.is_set()
    release.set()
    thread.join(5)
    assert not thread.is_alive() and isinstance(out[0], dict)
    with pytest.raises(LurkerError, match="WATCH_CHANGED"):
        client(instance, clock, lambda *a: SUCCESS).call(
            ENDPOINT, PARAMS, watch=(watch["id"], watch["generation"])
        )


def test_deadline_and_night_never_make_request(instance, clock):
    calls = []
    c = client(instance, clock, lambda *a: calls.append(1) or SUCCESS)
    with pytest.raises(LurkerError, match="TIME_SLICE_END"):
        c.call(ENDPOINT, PARAMS, deadline=clock.mono() + 34)
    clock.now = timestamp("02:00")
    with pytest.raises(LurkerError, match="QUIET_HOURS"):
        c.call(ENDPOINT, PARAMS, automatic=True)
    assert calls == []


def test_metadata_adapter_only_uses_list_cover_and_preserves_numeric_id(instance, watch, clock):
    from social_lurker.sources import publication

    item = {
        "aweme_id": 1234567890123456789,
        "create_time": clock.now,
        "author": {"sec_uid": watch["author_id"]},
        "desc": "文案",
        "share_url": "https://www.douyin.com/video/123",
        "video": {
            "cover": {"url_list": ["https://cdn.example.com/a.jpg"]},
            "play_addr": {"url_list": ["https://media.example.com/large.mp4"]},
        },
    }
    parsed = publication("douyin", item)
    assert parsed.work_id == "1234567890123456789" and parsed.cover_url
    assert publication("douyin", item, from_list=False).cover_url is None
    assert not hasattr(parsed, "video")
    assert TikHub(None).capability(instance.load(), "douyin", "normal") == "unverified"


@pytest.mark.parametrize(
    "endpoint,params",
    [
        (ENDPOINT, {"sec_user_id": "a", "url": "https://evil.example"}),
        ("/unknown", {}),
        (DY + "fetch_one_video_by_share_url", {"share_url": "https://127.0.0.1/"}),
    ],
)
def test_request_proxy_rejected_before_key_use(instance, clock, endpoint, params):
    instance.path(".env").unlink()
    with pytest.raises(LurkerError):
        client(instance, clock, lambda *a: SUCCESS).call(endpoint, params)


def test_no_daily_budget_stop_and_utc_statistics_do_not_reset_rate(instance, clock):
    with instance.transaction() as (db, _):
        db.execute("UPDATE runtime SET requests_reserved=1200")
    c = client(instance, clock, lambda *a: SUCCESS)
    c.call(ENDPOINT, PARAMS)
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT requests_reserved FROM runtime").fetchone()[0] == 1201
    from datetime import datetime

    clock.now = int(datetime.fromisoformat("2026-09-13T23:59:59+00:00").timestamp())
    c.call(ENDPOINT, PARAMS)
    clock.advance(1)
    c.call(ENDPOINT, PARAMS)
    with instance.transaction("read") as (db, _):
        row = db.execute(
            "SELECT requests_reserved,request_count_day,api_next_start_at_ms FROM runtime"
        ).fetchone()
        assert row[0] == 1 and row[1] == "2026-09-14" and row[2] >= clock.now * 1000 + 1000


def test_lower_backoff_and_time_slice_skip_without_new_network(instance, clock, watch):
    with instance.transaction() as (db, _):
        db.execute("UPDATE watches SET failure_count=2 WHERE id=?", (watch["id"],))
    with pytest.raises(LurkerError, match="UPSTREAM_TEMPORARY"):
        client(instance, clock, lambda *a: Response(503, {})).call(
            ENDPOINT, PARAMS, watch=(watch["id"], watch["generation"])
        )
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT api_next_start_at_ms FROM runtime").fetchone()[0] >= int(
            (clock.now + 600) * 1000
        )
    with pytest.raises(LurkerError, match="TIME_SLICE_END"):
        client(instance, clock, lambda *a: pytest.fail("backoff bypassed")).call(ENDPOINT, PARAMS)
