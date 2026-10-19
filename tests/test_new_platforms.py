import io
import json

import pytest
from test_transport_adapters import transport

from social_lurker.adapters.base import Author
from social_lurker.adapters.bilibili import Bilibili
from social_lurker.adapters.bilibili import parse_page as bl_page
from social_lurker.adapters.links import resolve_bilibili_link, source_link
from social_lurker.adapters.wechat_mp import WechatMP
from social_lurker.adapters.wechat_mp import parse_page as mp_page
from social_lurker.adapters.youtube import Youtube, detail
from social_lurker.cli import main
from social_lurker.errors import LurkerError
from social_lurker.profiles import FileRegistry
from social_lurker.transport import MP, Response, Transport

CID = "UC" + "a" * 22
VID = "abcDEF123_-"
BID = "BV1gKJ86JEuP"
MPID = "gh_account"


def ok(data):
    return Response(200, {"code": 200, "data": data})


def bl(data):
    return {"code": 0, "data": data}


def bl_data(aid=9007199254740999, owner=123):
    return bl(
        {
            "archives": [
                {
                    "aid": aid,
                    "bvid": BID,
                    "upMid": owner,
                    "title": "投稿",
                    "pubdate": 1609459200,
                    "pic": "http://i0.hdslb.com/cover.jpg",
                }
            ],
            "page": {"num": 1, "size": 20, "total": 1},
        }
    )


def mp_data(pid=MPID):
    return {
        "biz_username": pid,
        "is_end": 1,
        "count": 2,
        "next_offset": None,
        "articles": [
            {
                "app_msg_id": 123,
                "idx": idx,
                "title": "文章",
                "create_time": 1609459200,
                "url": f"http://mp.weixin.qq.com/s?__biz=YWJj&mid=123&idx={idx}&sn=abc",
                "content_text": "must never be retained",
            }
            for idx in (1, 2)
        ],
    }


def videos(token=None, ids=(VID,), channel=True):
    value = {
        "videos": [
            {
                "video_id": vid,
                "title": "视频",
                "url": "https://www.youtube.com/watch?v=" + vid,
                "published_time": "3 hours ago",
            }
            for vid in ids
        ],
        "continuation_token": token,
    }
    if channel:
        value["channel"] = {"id": CID}
    return value


def shorts(token=None, ids=()):
    return {
        "channel_id": CID,
        "shorts": [
            {"video_id": vid, "title": "Shorts", "video_url": "https://www.youtube.com/shorts/" + vid}
            for vid in ids
        ],
        "has_more": bool(token),
        "total_count": len(ids),
        "continuation_token": token,
    }


@pytest.mark.parametrize(
    "link,platform",
    [
        ("https://mp.weixin.qq.com/s/abc", "wechat_mp"),
        ("https://mp.weixin.qq.com/s?__biz=YWJj&amp;mid=123&amp;idx=1", "wechat_mp"),
        ("https://space.bilibili.com/123", "bilibili"),
        ("https://www.bilibili.com/video/" + BID, "bilibili"),
        ("https://b23.tv/abc", "bilibili"),
        ("https://youtube.com/@channel", "youtube"),
        ("https://www.youtube.com/channel/" + CID, "youtube"),
        ("https://youtu.be/" + VID, "youtube"),
        ("https://www.youtube.com/shorts/" + VID, "youtube"),
    ],
)
def test_source_routing(link, platform):
    assert source_link("关注这个 " + link)[0] == platform


@pytest.mark.parametrize(
    "link",
    [
        "https://mp.weixin.qq.com.evil.test/s/abc",
        "https://youtu.be.evil.test/abc",
        "https://youtube.com@evil.test/watch?v=" + VID,
        "https://youtube.com:443@evil.test/" + VID,
        "https://api.tikhub.io/account",
        "https://evil.youtube.com/watch?v=" + VID,
        "https://mp.weixin.qq.com/not-an-article",
    ],
)
def test_untrusted_link_rejected(link):
    with pytest.raises(LurkerError):
        source_link(link)


def test_mp_batch_identity_namespaced_and_no_body():
    a = Author("wechat_mp", MPID, "公众号")
    page = mp_page(mp_data(), a)
    assert [w.work_id for w in page.items] == [MPID + ":123:1", MPID + ":123:2"]
    assert all(w.url.startswith("https://mp.weixin.qq.com/") and w.kind == "text" for w in page.items)
    assert "must never" not in str(page)
    other = mp_page(mp_data("gh_other"), Author("wechat_mp", "gh_other", "另一个"))
    assert other.items[0].work_id != page.items[0].work_id


@pytest.mark.parametrize(
    "change,code",
    [
        ({"biz_username": "gh_other"}, "PAGE_IDENTITY_INVALID"),
        ({"articles": None}, "PAGE_INVALID"),
        ({"count": 8}, "PAGE_INVALID"),
        ({"is_end": "0"}, "PAGE_INVALID"),
        ({"is_end": 0, "next_offset": ""}, "CURSOR_INVALID"),
    ],
)
def test_mp_invalid_page(change, code):
    with pytest.raises(LurkerError) as e:
        mp_page({**mp_data(), **change}, Author("wechat_mp", MPID, "公众号"))
    assert e.value.code == code


def test_mp_cursor_is_post_body_and_no_replay(tmp_path):
    data = mp_data()
    data.update(is_end=0, next_offset="next+/==")
    t, _, calls, _ = transport(
        tmp_path,
        [
            ok({"content": {"user_name": MPID, "nick_name": "公众号"}}),
            ok(data),
            ok({**mp_data(), "articles": [], "count": 0}),
        ],
    )
    adapter = WechatMP(t)
    a = adapter.resolve_author("https://mp.weixin.qq.com/s/abc")
    first = adapter.fetch_page(a, None)
    last = adapter.fetch_page(a, first.next_cursor)
    assert first.has_more and not last.has_more
    assert json.loads(calls[-1][1][3])["offset"] == "next+/=="
    assert calls[-1][1][0] == "POST" and calls[-1][1][1].endswith(MP + "fetch_account_articles")


def test_bilibili_nested_success_and_owner():
    a = Author("bilibili", "123", "UP")
    page = bl_page(bl_data(), a, 1)
    assert page.items[0].work_id == "9007199254740999"
    assert page.items[0].url == "https://www.bilibili.com/video/" + BID
    assert page.items[0].cover_url.startswith("https:") and not page.has_more
    for bad in (bl_data(owner=124), {"code": -352, "data": None}, bl({"archives": None, "page": {}})):
        with pytest.raises(LurkerError):
            bl_page(bad, a, 1)


def test_bilibili_unbounded_page_and_checked_short_redirect(tmp_path):
    a = Author("bilibili", "123", "UP")
    data = bl_data()
    data["data"]["page"].update(num=101, total=2021)
    t, _, calls, _ = transport(tmp_path, [ok(data)])
    assert Bilibili(t).fetch_page(a, "101").next_cursor == "102"
    assert "pn=101" in calls[0][1][1]
    assert (
        resolve_bilibili_link("https://b23.tv/abc", lambda _: "https://space.bilibili.com/123")
        == "https://space.bilibili.com/123"
    )
    with pytest.raises(LurkerError):
        resolve_bilibili_link("https://b23.tv/abc", lambda _: "https://evil.test/")


def test_bilibili_short_requests_do_not_forward_key(tmp_path):
    t, _, calls, stats = transport(tmp_path, [ok(bl({"mid": 123, "name": "UP"}))])

    def share(method, url, headers, body, timeout):
        assert "Authorization" not in headers and "Cookie" not in headers
        return Response(302, None, {"Location": "https://space.bilibili.com/123"})

    t.share_send = share
    assert Bilibili(t).resolve_author("https://b23.tv/abc").author_id == "123"
    assert len(calls) == stats["requests"] == 1


def test_youtube_composite_continuation_binds_channel_and_skips_finished_feed(tmp_path):
    t, _, calls, _ = transport(
        tmp_path, [ok(videos("v-next")), ok(shorts()), ok(videos(ids=("newDEF123_-",), channel=False))]
    )
    a = Author("youtube", CID, "频道")
    adapter = Youtube(t)
    first = adapter.fetch_page(a, None)
    assert first.items[0].published_at is None
    last = adapter.fetch_page(a, first.next_cursor)
    assert not last.has_more and len(calls) == 3
    assert "get_channel_shorts" in calls[1][1][1]
    assert "continuation_token=v-next" in calls[2][1][1]
    with pytest.raises(LurkerError):
        adapter.fetch_page(Author("youtube", "UC" + "b" * 22, "其他"), first.next_cursor)


def test_youtube_shorts_own_cursor_and_duplicate_video_id(tmp_path):
    t, _, calls, _ = transport(
        tmp_path, [ok(videos()), ok(shorts("short-next", (VID,))), ok(shorts(ids=("newDEF123_-",)))]
    )
    a = Author("youtube", CID, "频道")
    adapter = Youtube(t)
    first = adapter.fetch_page(a, None)
    assert len(first.validated(a)) == 1
    assert not adapter.fetch_page(a, first.next_cursor).has_more
    assert "get_channel_shorts" in calls[-1][1][1] and "short-next" in calls[-1][1][1]


@pytest.mark.parametrize(
    "bad",
    [
        videos(channel=False),
        {**videos(), "channel": {"id": "UC" + "b" * 22}},
        {**videos(), "videos": None},
        {**videos("token"), "videos": []},
    ],
)
def test_youtube_invalid_first_video_page(tmp_path, bad):
    t, _, _, _ = transport(tmp_path, [ok(bad), ok(shorts())])
    with pytest.raises(LurkerError):
        Youtube(t).fetch_page(Author("youtube", CID, "频道"), None)


def test_youtube_detail_identity_and_exact_time():
    data = {
        "playerResponse": {
            "videoDetails": {"videoId": VID, "channelId": CID, "title": "视频"},
            "microformat": {"playerMicroformatRenderer": {"publishDate": "2026-10-19T10:00:00+08:00"}},
        }
    }
    work, _ = detail(data, VID)
    assert work.published_at == "2026-10-19T02:00:00Z"
    data["playerResponse"]["microformat"]["playerMicroformatRenderer"]["publishDate"] = "2026-10-19"
    assert detail(data, VID)[0].published_at is None
    with pytest.raises(LurkerError):
        detail(data, "newDEF123_-")


def test_cli_wires_all_three_adapters_and_databases(tmp_path):
    registry = FileRegistry(tmp_path / "data")
    registry.create("测试")
    replies = [
        ok({"content": {"user_name": MPID, "nick_name": "公众号"}}),
        ok(mp_data()),
        ok(bl({"mid": 123, "name": "UP"})),
        ok(bl_data()),
        ok({"channel_id": CID, "title": "频道"}),
        ok(videos()),
        ok(shorts()),
    ]
    calls = []

    def factory(secrets, stats, config, **kwargs):
        def send(*args):
            calls.append(args)
            return replies.pop(0)

        return Transport(secrets, stats, config, send=send, sleep=lambda _: None, **kwargs)

    from social_lurker.config import FileSecrets

    args = ["--install-root", str(tmp_path / "install"), "--profile=测试"]
    for link, platform in [
        ("https://mp.weixin.qq.com/s/abc", "wechat_mp"),
        ("https://space.bilibili.com/123", "bilibili"),
        ("https://youtube.com/channel/" + CID, "youtube"),
    ]:
        out = io.StringIO()
        assert (
            main(
                args + ["add", link],
                registry=registry,
                secrets=FileSecrets(tmp_path / "no-key", {"TIKHUB_API_KEY": "fixture"}),
                transport_factory=factory,
                stdout=out,
            )
            == 0
        )
        records = [json.loads(line) for line in out.getvalue().splitlines()]
        assert records[0]["payload"]["platform"] == platform
        assert records[-1]["summary"]["pages_committed"] == 1
        assert not any(row["type"] == "work" for row in records)
    assert not replies and len(calls) == 7
    out = io.StringIO()
    assert main(args + ["list"], registry=registry, stdout=out) == 0
    assert len(json.loads(out.getvalue().splitlines()[0])["payload"]["authors"]) == 3


def test_bilibili_proven_collaboration_excluded_and_cached(tmp_path):
    owner = Author("bilibili", "123", "主作者")
    listing = bl_data(owner=456)
    listing["data"]["page"]["total"] = 40
    proof = bl({"aid": 9007199254740999, "bvid": BID, "owner": {"mid": 456}, "staff": [{"mid": 123}]})
    t, _, calls, _ = transport(tmp_path, [ok(listing), ok(proof), ok(listing)])
    adapter = Bilibili(t)
    page = adapter.fetch_page(owner, None)
    assert page.items == [] and page.excluded_ids == ("9007199254740999",)
    assert page.has_more
    assert adapter.fetch_page(owner, None) == page
    assert len(calls) == 3


@pytest.mark.parametrize("staff", [None, [], [{"mid": 789}]])
def test_bilibili_unproven_foreign_owner_rejects_whole_page(tmp_path, staff):
    proof = bl({"aid": 9007199254740999, "bvid": BID, "owner": {"mid": 456}, "staff": staff})
    t, _, _, _ = transport(tmp_path, [ok(bl_data(owner=456)), ok(proof)])
    with pytest.raises(LurkerError) as error:
        Bilibili(t).fetch_page(Author("bilibili", "123", "作者"), None)
    assert error.value.code == "PAGE_IDENTITY_INVALID"


def test_youtube_bad_second_feed_never_commits_first_feed(tmp_path):
    from social_lurker.db import Database
    from social_lurker.output import Output, summary
    from social_lurker.service import Service

    t, _, calls, _ = transport(tmp_path, [ok(videos()), ok({**shorts(), "channel_id": "UC" + "b" * 22})])
    db = Database(tmp_path / "db.sqlite", "测试")
    a = Author("youtube", CID, "频道")
    db.upsert_page(a, [], baseline=True)
    out = io.StringIO()
    stats = summary()
    service = Service(db, {}, Output(stream=out), stats)
    with pytest.raises(LurkerError) as error:
        service.scan(a, Youtube(t))
    assert error.value.code == "PAGE_IDENTITY_INVALID"
    assert db.authors()[0]["works_count"] == 0 and stats["pages_committed"] == 0
    assert len(calls) == 2 and out.getvalue() == ""
    db.close()


def test_mp_link_batch_mismatch_rejects_page():
    data = mp_data()
    data["articles"][1]["url"] = "https://mp.weixin.qq.com/s?__biz=YWJj&mid=999&idx=2"
    with pytest.raises(LurkerError) as error:
        mp_page(data, Author("wechat_mp", MPID, "公众号"))
    assert error.value.code == "PAGE_IDENTITY_INVALID"


def test_bilibili_collaboration_detail_must_match_declared_owner(tmp_path):
    proof = bl({"aid": 9007199254740999, "bvid": BID, "owner": {"mid": 789}, "staff": [{"mid": 123}]})
    t, _, _, _ = transport(tmp_path, [ok(bl_data(owner=456)), ok(proof)])
    with pytest.raises(LurkerError) as error:
        Bilibili(t).fetch_page(Author("bilibili", "123", "作者"), None)
    assert error.value.code == "PAGE_IDENTITY_INVALID"


def test_mp_long_link_must_match_canonical_account(tmp_path):
    payload = {
        "content": {
            "user_name": MPID,
            "mid": 123,
            "idx": 1,
            "link": "https://mp.weixin.qq.com/s?__biz=OTHER&mid=123&idx=1",
        }
    }
    t, _, _, _ = transport(tmp_path, [ok(payload)])
    with pytest.raises(LurkerError) as error:
        WechatMP(t).resolve_author("https://mp.weixin.qq.com/s?__biz=YWJj&mid=123&idx=1")
    assert error.value.code == "PAGE_IDENTITY_INVALID"
