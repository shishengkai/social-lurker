import json

import pytest
from conftest import ingest, publication
from test_delivery import receipt
from test_discovery import rows

from social_lurker.cli import execute
from social_lurker.config import automatic_gate
from social_lurker.delivery import Delivery, format_payload
from social_lurker.errors import LurkerError
from social_lurker.http import Client, Response
from social_lurker.operations import bind, check, routine_plan
from social_lurker.sources import ADAPTER_VERSION, WX, TikHub, validate_params
from social_lurker.sources import publication as parse_publication
from social_lurker.util import atomic_json, public_cover_url, public_url


def test_simplified_detail_error_uses_one_raw_retry_and_current_share_identity(instance, clock):
    calls = []
    author_id = "fixture@finder"
    raw = {
        "id": 2**63 + 42,
        "username": author_id,
        "nickname": "作者",
        "createtime": int(clock()),
        "objectDesc": {"description": "标题", "media": [{"url": "https://media.example.com/video"}]},
    }

    def send(endpoint, params, *args):
        calls.append((endpoint, params))
        if endpoint.endswith("fetch_user_profile"):
            data = {"username": author_id, "nickname": "作者"}
        else:
            data = (
                raw
                if params["raw"]
                else {"message": "fixture simplified response error", "debug_id": "private"}
            )
        return Response(200, {"code": 200, "data": data})

    source = TikHub(Client(instance, send=send, sleep=clock.sleep, monotonic=clock.mono))
    author = source.resolve_author("https://weixin.qq.com/sph/fixture")
    assert author.author_id == author_id
    assert [(e.rsplit("/", 1)[-1], p.get("raw")) for e, p in calls] == [
        ("fetch_video_detail", False),
        ("fetch_video_detail", True),
    ]
    parsed = parse_publication("wechat_channels", raw, from_list=False)
    assert parsed.work_id == str(2**63 + 42) and parsed.cover_url is None
    assert "media" not in vars(parsed) and "debug_id" not in vars(author)


def test_share_detail_author_conflict_cannot_be_replaced_with_remembered_author(instance, clock):
    raw = {
        "id": "42",
        "username": "current@finder",
        "nickname": "作者",
        "contact": {"username": "other@finder"},
    }
    client = Client(
        instance,
        send=lambda *a: Response(200, {"code": 200, "data": raw}),
        sleep=clock.sleep,
        monotonic=clock.mono,
    )
    with pytest.raises(LurkerError, match="PAGE_IDENTITY_INVALID"):
        TikHub(client).resolve_author("https://weixin.qq.com/sph/fixture")


def test_detail_error_retry_is_bounded_even_when_raw_response_fails(instance, clock):
    calls = []

    def send(endpoint, params, *args):
        calls.append(params["raw"])
        return Response(200, {"code": 200, "data": {"message": "fixture failure"}})

    client = Client(instance, send=send, sleep=clock.sleep, monotonic=clock.mono)
    with pytest.raises(LurkerError, match="WECHAT_DETAIL_UNAVAILABLE"):
        TikHub(client).resolve_author("https://weixin.qq.com/sph/fixture")
    assert calls == [False, True]


def test_detail_auth_failure_does_not_fallback_or_bypass_hold(instance, clock):
    calls = []
    client = Client(
        instance,
        send=lambda *a: calls.append(a[0]) or Response(403, {}),
        sleep=clock.sleep,
        monotonic=clock.mono,
    )
    with pytest.raises(LurkerError, match="API_PERMISSION"):
        TikHub(client).resolve_author("https://weixin.qq.com/sph/fixture")
    assert calls == [WX + "fetch_video_detail"]
    validate_params(
        WX + "fetch_video_detail", {"share_url": "https://weixin.qq.com/sph/fixture", "raw": True}
    )
    with pytest.raises(LurkerError, match="REQUEST_PARAMS_INVALID"):
        validate_params(WX + "fetch_user_videos", {"username": "fixture", "raw": True})


def test_list_cover_signature_is_scoped_to_image_cdn_and_survives_storage(instance, watch, clock):
    image = "https://wxapp.tc.qq.com/image?encfilekey=fixture"
    item = {
        "id": "42",
        "username": "fixture@finder",
        "create_time": int(clock()),
        "media": {
            "cover_url": image,
            "cover_url_token": "&token=public-image-signature",
            "url_token": "private-video",
        },
    }
    parsed = parse_publication("wechat_channels", item)
    assert parsed.cover_url == image + "&token=public-image-signature"
    assert public_url(parsed.cover_url) is None  # Original-link policy is unchanged.
    assert (
        public_cover_url(parsed.cover_url)
        and parse_publication("wechat_channels", item, from_list=False).cover_url is None
    )
    ingest(instance, watch, [publication(watch, 1, clock.now, cover_url=parsed.cover_url)])
    permit = Delivery(instance).next()
    assert permit["payload"]["images"][0]["url"] == parsed.cover_url
    assert "private-video" not in json.dumps(permit)
    for url in [
        "https://evil.example/image?token=abc",
        image + "&token=abc&api_key=secret",
        image + "&token=one&token=two",
        "http://wxapp.tc.qq.com/image?token=abc",
        "https://wxapp.tc.qq.com@evil.example/image?token=abc",
    ]:
        assert public_cover_url(url) is None
    item["media"]["cover_url_token"] = "&token=abc&cookie=secret"
    assert parse_publication("wechat_channels", item).cover_url is None
    item["cover_img_url"] = "https://cdn.example.com/public.jpg"
    assert parse_publication("wechat_channels", item).cover_url == item["cover_img_url"]


def test_length_preflight_precedes_any_test_api_call(instance, watch, monkeypatch):
    settings = instance.load()
    del settings["host"]["evidence_ref"]["host"]["length_evidence"]
    atomic_json(instance.path("settings.json"), settings)
    monkeypatch.setattr(
        "social_lurker.monitor.Monitor.test_latest",
        lambda *a: pytest.fail("API work started before preflight"),
    )
    assert not check(instance)["test_ready"]
    with pytest.raises(LurkerError, match="HOST_LENGTH_UNVERIFIED"):
        execute(instance, ("watch", "test-latest"), {"protocol": 1, "watch_id": watch["id"]})
    with pytest.raises(LurkerError, match="LENGTH_PROOF_REQUIRED"):
        bind(
            instance,
            {"evidence": "generic-ref", "host": {"max_message_length": 10000, "length_unit": "unicode"}},
        )
    bind(
        instance,
        {
            "evidence": "fixture-ref",
            "host": {
                "max_message_length": 4000,
                "length_unit": "unicode",
                "length_evidence": "fixture-length-doc",
            },
        },
    )
    assert check(instance)["test_ready"]


def test_image_trial_requires_scoped_permit_and_sent_render_proof(instance, watch, clock):
    settings = instance.load()
    settings["host"]["evidence_ref"]["host"]["images_verified"] = False
    atomic_json(instance.path("settings.json"), settings)
    ingest(
        instance,
        watch,
        [
            publication(watch, 1, clock.now),
            publication(watch, 2, clock.now, cover_url="https://cdn.example.com/cover.jpg"),
        ],
    )
    target = next(r for r in rows(instance) if r["work_id"] == "2")
    with instance.transaction() as (db, _):
        db.execute("UPDATE updates SET reason='test' WHERE id=?", (target["id"],))
    d = Delivery(instance)
    with pytest.raises(LurkerError, match="IMAGE_TEST_SCOPE_REQUIRED"):
        d.next(test_images=True)
    p = d.next(foreground_test=True, test_images=True, update_id=target["id"])
    assert p["payload"]["images"] and p["object_id"] == target["id"]
    assert not instance.load()["host"]["evidence_ref"]["host"]["images_verified"]
    proof = {
        "evidence": "fixture-message",
        "host": {
            "images_verified": True,
            "image_update_id": target["id"],
            "image_evidence": "fixture-render-check",
        },
    }
    with pytest.raises(LurkerError, match="IMAGE_PROOF_REQUIRED"):
        bind(instance, proof)
    d.report(receipt(p, clock))
    bind(instance, proof)
    assert check(instance)["images_verified"]
    assert d.next(foreground_test=True) is None  # Does not consume normal notifications.
    assert next(r for r in rows(instance) if r["work_id"] == "1")["state"] == "queued"


def test_image_trial_without_cover_does_not_freeze_or_cancel_text_fallback(instance, watch, clock):
    ingest(instance, watch, [publication(watch, 1, clock.now)])
    with instance.transaction() as (db, _):
        db.execute("UPDATE updates SET reason='test'")
    uid = rows(instance)[0]["id"]
    with pytest.raises(LurkerError, match="TEST_COVER_UNAVAILABLE"):
        Delivery(instance).next(foreground_test=True, test_images=True, update_id=uid)
    assert rows(instance)[0]["state"] == "queued" and rows(instance)[0]["attempt_id"] is None
    permit = Delivery(instance).next(foreground_test=True, update_id=uid)
    assert permit["payload"]["images"] == []
    Delivery(instance).report(receipt(permit, clock))
    with pytest.raises(LurkerError, match="IMAGE_PROOF_REQUIRED"):
        bind(
            instance,
            {
                "evidence": "fixture",
                "host": {"images_verified": True, "image_update_id": uid, "image_evidence": "not-an-image"},
            },
        )


def test_routine_can_record_real_pause_with_active_watches_and_requires_readiness(instance, watch):
    settings = instance.load()
    settings["host"]["quiet_execution_verified_at"] = None
    atomic_json(instance.path("settings.json"), settings)
    plan = routine_plan(instance)
    assert plan["requested_active"] and not plan["active"]
    bind(
        instance,
        {
            "evidence": "fixture-observed-paused",
            "host": {"routine_active": False, "routine_binding_hash": plan["binding_hash"]},
        },
    )
    assert routine_plan(instance)["observed_active"] is False
    assert routine_plan(instance)["synchronized"]
    with pytest.raises(LurkerError, match="ROUTINE_ACTIVATION_UNVERIFIED"):
        bind(
            instance,
            {
                "evidence": "bad-enable",
                "host": {"routine_active": True, "routine_binding_hash": plan["binding_hash"]},
            },
        )
    with pytest.raises(LurkerError, match="HOST_AUTOMATIC_UNVERIFIED"):
        automatic_gate(instance.load())
    result = bind(
        instance,
        {
            "evidence": "fixture-silence",
            "host": {"quiet_execution_verified": True},
            "sources": {
                "douyin:normal": {
                    "adapter_version": ADAPTER_VERSION,
                    "level": "recent_pages_verified",
                    "evidence": "fixture-pages",
                }
            },
        },
    )
    assert result["routine"]["active"] and result["mode"] == "foreground_only"
    assert instance.load()["host"]["evidence_ref"]["host"]["routine_evidence"] == "fixture-observed-paused"
    assert "HOST_ROUTINE_UNSYNCED" in result["missing"]
    result = bind(
        instance,
        {
            "evidence": "fixture-observed-running",
            "host": {"routine_active": True, "routine_binding_hash": result["routine"]["binding_hash"]},
        },
    )
    assert result["mode"] == "automatic_ready"
    bind(instance, {"evidence": "fixture-silence-revoked", "host": {"quiet_execution_verified": False}})
    assert instance.load()["host"]["quiet_execution_verified_at"] is None


def test_card_hierarchy_and_hostile_title_cannot_inject_links(instance, watch, clock):
    row = vars(publication(watch, 1, clock.now, title="标题 **伪造** [跳转](https://evil.example)\n下一段"))
    text = format_payload(row, instance.load())["text"]
    assert text.startswith("**标题 ") and "\\*\\*伪造\\*\\*" in text
    assert "作者甲 · 抖音" in text and "北京时间" in text and "Asia/Shanghai" not in text
    assert text.endswith("[打开原作品](https://www.douyin.com/video/1)")
