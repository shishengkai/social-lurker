import json
import socket
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from social_lurker.adapters.base import Author
from social_lurker.adapters.douyin import Douyin
from social_lurker.adapters.douyin import parse_page as dy_page
from social_lurker.adapters.links import source_link
from social_lurker.adapters.wechat import Wechat
from social_lurker.adapters.wechat import parse_page as wx_page
from social_lurker.config import Config, FileSecrets
from social_lurker.errors import LurkerError
from social_lurker.output import summary
from social_lurker.transport import API_ROOT, DY, WX, Response, Transport, retry_after


class Clock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def transport(tmp_path, responses, config=Config()):
    clock, calls, stats = Clock(), [], summary()

    def send(*args):
        calls.append((clock.now(), args))
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    t = Transport(
        FileSecrets(tmp_path / "key.json", {"TIKHUB_API_KEY": "fixture-private"}),
        stats,
        config,
        send=send,
        sleep=clock.sleep,
        monotonic=clock.now,
    )
    return t, clock, calls, stats


def ok(data):
    return Response(200, {"code": 200, "data": data})


def test_serial_rate_all_endpoints_and_params(tmp_path):
    t, _, calls, stats = transport(tmp_path, [ok({}), ok({}), ok({})])
    t.call(DY + "handler_user_profile", {"sec_user_id": "a"})
    t.call(WX + "fetch_user_videos", {"username": "b", "last_buffer": "+//="})
    t.call(DY + "fetch_one_video", {"aweme_id": "3"})
    assert [c[0] for c in calls] == [0, 1, 2] and stats["requests"] == 3
    assert calls[0][1][1].startswith(API_ROOT)
    assert calls[1][1][0] == "POST" and json.loads(calls[1][1][3])["last_buffer"] == "+//="


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(401, {"secret": "fixture-private"}), "AUTH_FAILED"),
        (Response(200, {"code": 403, "data": None}), "AUTH_FAILED"),
        (Response(402, {}), "QUOTA_UNAVAILABLE"),
        (Response(429, {}, {"Retry-After": "31"}), "RATE_LIMITED"),
        (ok(None), "PAGE_INVALID"),
        (ok({"status_code": 9}), "PAGE_INVALID"),
        (Response(200, {"code": 200}), "PAGE_INVALID"),
        (Response(302, None), "PAGE_INVALID"),
        (Response(500, None), "NETWORK_ERROR"),
        (socket.timeout(), "REQUEST_TIMEOUT"),
    ],
)
def test_errors_never_empty_or_leak_and_global_stops(tmp_path, response, code):
    t, _, calls, _ = transport(tmp_path, [response, ok({})])
    with pytest.raises(LurkerError) as caught:
        t.call(DY + "fetch_one_video", {"aweme_id": "1"})
    assert caught.value.code == code and "fixture-private" not in json.dumps(caught.value.public())
    if caught.value.global_stop:
        with pytest.raises(LurkerError):
            t.call(DY + "fetch_one_video", {"aweme_id": "1"})
        assert len(calls) == 1


def test_429_wait_once_then_stop_and_bounded_retries(tmp_path):
    t, clock, calls, stats = transport(
        tmp_path, [Response(429, {}, {"Retry-After": "2"}), Response(429, {}, {"Retry-After": "1"})]
    )
    with pytest.raises(LurkerError) as caught:
        t.call(DY + "fetch_one_video", {})
    assert caught.value.code == "RATE_LIMITED" and len(calls) == 2
    assert clock.value == 2 and stats["requests"] == 2
    t, _, calls, _ = transport(tmp_path, [Response(500, None), ok({})], Config(retries=1))
    assert t.call(DY + "fetch_one_video", {}) == {} and len(calls) == 2
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10), usegmt=True)
    assert 8 <= retry_after({"Retry-After": date}) <= 10
    assert retry_after({"Retry-After": "invalid"}) == 1


def dy_item(wid="9007199254740999", aid="a"):
    return {
        "aweme_id": wid,
        "author": {"sec_uid": aid, "nickname": "甲"},
        "desc": "原题",
        "create_time": 1609459200,
        "aweme_type": 0,
        "share_url": "https://www.douyin.com/video/123",
    }


def wx_item(wid=18446744073709551610, aid="a@finder"):
    return {
        "id": wid,
        "username": aid,
        "nickname": "甲",
        "title": "原题",
        "create_time": 1609459200,
        "media": {
            "full_url": "https://cdn.example/video?token=fixture-private",
            "decode_key": "fixture-private",
        },
    }


def test_documented_wechat_nonempty_tail_empty_tail_more_and_large_ids():
    author = Author("wechat_channels", "a@finder", "甲")
    for videos in ([wx_item()], []):
        page = wx_page(
            {
                "username": author.author_id,
                "videos": videos,
                "count": len(videos),
                "up_continue": 0,
                "last_buffer": "unused",
            },
            author,
        )
        assert page.has_more is False
        if videos:
            assert page.items[0].work_id == "18446744073709551610"
            assert "fixture-private" not in repr(page)
    page = wx_page(
        {
            "username": author.author_id,
            "videos": [wx_item()],
            "count": 1,
            "up_continue": 1,
            "last_buffer": "+//=",
        },
        author,
    )
    assert page.next_cursor == "+//="


@pytest.mark.parametrize(
    "change",
    [
        {"username": "wrong"},
        {"videos": None},
        {"up_continue": None},
        {"count": 2},
        {"up_continue": 1, "last_buffer": ""},
    ],
)
def test_wechat_invalid_envelopes(change):
    data = {"username": "a@finder", "videos": [wx_item()], "count": 1, "up_continue": 0}
    with pytest.raises(LurkerError):
        wx_page({**data, **change}, Author("wechat_channels", "a@finder", "甲"))


def test_douyin_primary_cooperation_and_unknown_owner():
    author = Author("douyin", "a", "甲")
    data = {"sec_uid": "a", "aweme_list": [dy_item()], "has_more": 0}
    assert dy_page(data, author).items[0].work_id == "9007199254740999"
    collaboration = dy_item("other", "b")
    collaboration["cooperation_info"] = {"co_creators": [{"sec_uid": "a"}]}
    page = dy_page({**data, "aweme_list": [dy_item(), collaboration]}, author)
    assert page.excluded_ids == ("other",) and len(page.items) == 1
    del collaboration["cooperation_info"]
    with pytest.raises(LurkerError):
        dy_page({**data, "aweme_list": [collaboration]}, author)


def test_adapters_resolve_fetch_and_one_enrichment_path(tmp_path):
    user = {"user": {"sec_uid": "a", "nickname": "甲"}}
    list_data = {"sec_uid": "a", "aweme_list": [dy_item()], "has_more": 0}
    t, _, calls, _ = transport(tmp_path, [ok(user), ok(list_data)])
    d = Douyin(t)
    author = d.resolve_author("https://www.douyin.com/user/a")
    work = d.fetch_page(author, None).items[0]
    assert d.enrich_metadata(author, work) == work and len(calls) == 2
    assert "count=20" in calls[1][1][1] and "sort_type=0" in calls[1][1][1]
    t, _, calls, _ = transport(
        tmp_path,
        [
            ok(wx_item()),
            ok({"username": "a@finder", "videos": [wx_item()], "count": 1, "up_continue": 0}),
            ok({"object_id": "18446744073709551610", "share_url": "https://weixin.qq.com/sph/x"}),
        ],
    )
    wx = Wechat(t)
    author = wx.resolve_author("https://weixin.qq.com/sph/x")
    work = wx.fetch_page(author, None).items[0]
    assert wx.enrich_metadata(author, work).url == "https://weixin.qq.com/sph/x"
    assert len(calls) == 3 and all(json.loads(c[1][3])["raw"] is False for c in calls)


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example/x",
        "https://douyin.com.evil.example/x",
        "https://www.douyin.com/",
        "http://www.douyin.com/user/a",
        "https://weixin.qq.com/other/x",
        "https://a@douyin.com/user/x",
    ],
)
def test_reject_source_forms_before_network(link):
    with pytest.raises(LurkerError):
        source_link(link)


def test_source_link_allowed_and_precision_safety():
    assert source_link("分享 https://v.douyin.com/abc/ 。")[0] == "douyin"
    assert source_link("https://weixin.qq.com/sph/abc")[0] == "wechat_channels"
    bad = dy_item()
    bad["aweme_id"] = 9007199254740999.0
    with pytest.raises(LurkerError):
        dy_page({"sec_uid": "a", "aweme_list": [bad], "has_more": 0}, Author("douyin", "a", "甲"))
