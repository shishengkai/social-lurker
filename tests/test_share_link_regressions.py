import urllib.error

import pytest
from test_transport_adapters import dy_item, ok, transport

from social_lurker.adapters.douyin import Douyin
from social_lurker.adapters.links import resolve_douyin_link
from social_lurker.config import Config
from social_lurker.errors import LurkerError
from social_lurker.transport import BROWSER_USER_AGENT, DY, NoRedirect, Response, send_redirect

SHORT = "https://v.douyin.com/wo45Ud3q9H4/"
HOME = "https://www.iesdouyin.com/share/user/author-a?from=share"


def test_homepage_short_link_add_only_baseline(env, tmp_path):
    *_, stream, stats, _, _, service = env[3:]
    t, clock, api_calls, _ = transport(
        tmp_path,
        [
            ok({"user": {"sec_uid": "author-a", "nickname": "甲"}}),
            ok(
                {
                    "sec_uid": "author-a",
                    "aweme_list": [dy_item(aid="author-a")],
                    "has_more": 1,
                    "max_cursor": 123,
                }
            ),
        ],
    )
    public_calls = []

    def share(*args):
        public_calls.append((clock.now(), args))
        assert "Authorization" not in args[2] and "Cookie" not in args[2]
        assert args[2]["User-Agent"] == BROWSER_USER_AGENT
        return Response(302, None, {"Location": HOME})

    t.share_send = share
    result = service.add(SHORT, Douyin(t))
    assert result["baseline_count"] == 1 and stream.getvalue() == ""
    assert len(public_calls) == 1 and [x[0] for x in api_calls] == [1, 2]
    assert "handler_user_profile" in api_calls[0][1][1]
    assert "fetch_user_post_videos" in api_calls[1][1][1]
    assert all(x[1][2]["User-Agent"] == BROWSER_USER_AGENT for x in api_calls)
    assert t.stats["requests"] == 2


@pytest.mark.parametrize(
    "link",
    [
        "https://www.douyin.com/user/author-a",
        HOME,
        "https://iesdouyin.com/share/user/author-a/",
    ],
)
def test_direct_author_no_public_request(link):
    assert resolve_douyin_link(link, lambda _: pytest.fail("unexpected HTTP")) == ("user", "author-a")


def test_short_work_retains_documented_detail_api(tmp_path):
    t, _, calls, _ = transport(tmp_path, [ok({"aweme_detail": dy_item()})])
    t.share_send = lambda *args: Response(302, None, {"location": "https://www.douyin.com/video/123"})
    assert Douyin(t).resolve_author(SHORT).author_id == "a"
    assert "fetch_one_video_by_share_url" in calls[0][1][1]


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/user/a",
        "https://douyin.com.evil.example/user/a",
        "http://www.douyin.com/user/a",
        "https://secret@www.douyin.com/user/a",
        "https://127.0.0.1/user/a",
        "https://weixin.qq.com/sph/a",
        "https://www.douyin.com/unknown/a",
        "https://www.douyin.com/user/a\n",
        "",
        None,
    ],
)
def test_bad_redirect_rejected_before_second_request(location):
    calls = []

    def redirect(link):
        calls.append(link)
        return location

    with pytest.raises(LurkerError) as caught:
        resolve_douyin_link(SHORT, redirect)
    assert caught.value.code == "SOURCE_LINK_INVALID" and calls == [SHORT]


def test_redirect_relative_cycle_and_bound():
    locations = iter(["/next/", HOME])
    assert resolve_douyin_link(SHORT, lambda _: next(locations)) == ("user", "author-a")
    calls = []

    def cycle(link):
        calls.append(link)
        return SHORT

    with pytest.raises(LurkerError):
        resolve_douyin_link(SHORT, cycle)
    assert len(calls) == 1
    calls.clear()

    def unbounded(link):
        calls.append(link)
        return f"https://v.douyin.com/next{len(calls)}/"

    with pytest.raises(LurkerError):
        resolve_douyin_link(SHORT, unbounded)
    assert len(calls) == 5


@pytest.mark.parametrize(
    "body,code",
    [
        (None, "HTTP_BLOCKED"),
        ({"message": "fixture-private Cloudflare 1010"}, "HTTP_BLOCKED"),
        ({"code": 403}, "AUTH_FAILED"),
    ],
)
def test_gateway_403_is_not_key_failure_and_stops(tmp_path, body, code):
    t, _, calls, _ = transport(tmp_path, [Response(403, body)], Config(retries=2))
    for _ in range(2):
        with pytest.raises(LurkerError) as caught:
            t.call(DY + "handler_user_profile", {})
        assert caught.value.code == code
        assert "fixture-private" not in str(caught.value.public())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(403, None), "HTTP_BLOCKED"),
        (Response(200, None), "SOURCE_LINK_INVALID"),
        (Response(429, None), "RATE_LIMITED"),
        (Response(503, None), "NETWORK_ERROR"),
        (TimeoutError(), "REQUEST_TIMEOUT"),
        (urllib.error.URLError("fixture-private"), "NETWORK_ERROR"),
    ],
)
def test_public_resolution_no_key_or_supplier_calls(tmp_path, response, code):
    t, _, api_calls, stats = transport(tmp_path, [])
    t.secrets.get_key = lambda: pytest.fail("credential read")

    def share(*_):
        if isinstance(response, Exception):
            raise response
        return response

    t.share_send = share
    with pytest.raises(LurkerError) as caught:
        Douyin(t).resolve_author(SHORT)
    assert caught.value.code == code and api_calls == [] and stats["requests"] == 0


def test_headers_only_no_automatic_redirect(monkeypatch):
    class Reply:
        code = 302
        headers = {"Location": HOME}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def read(self, *_):
            pytest.fail("page body download")

    reply = Reply()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == SHORT and request.get_method() == "GET"
            assert request.get_header("Authorization") is None
            return reply

    def build(handler):
        assert isinstance(handler, NoRedirect)
        return Opener()

    monkeypatch.setattr("urllib.request.build_opener", build)
    assert send_redirect("GET", SHORT, {}, None, 60).headers["Location"] == HOME
    assert reply.closed


def test_output_close_prevents_public_request(tmp_path):
    t, _, _, _ = transport(tmp_path, [])
    t.share_send = lambda *_: pytest.fail("request after output close")

    def closed():
        raise BrokenPipeError()

    t.before_request = closed
    with pytest.raises(BrokenPipeError):
        t.redirect(SHORT)
