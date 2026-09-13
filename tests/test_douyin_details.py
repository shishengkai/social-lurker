import pytest

from social_lurker.errors import LurkerError
from social_lurker.http import Client, Response
from social_lurker.sources import DY, TikHub, douyin_detail, validate_probe


@pytest.mark.parametrize("reason", [5, 8, 10])
def test_filtered_share_explains_unavailable_without_profile_or_retry(instance, clock, reason):
    calls = []

    def send(endpoint, *args):
        calls.append(endpoint)
        return Response(
            200,
            {
                "code": 200,
                "data": {"status_code": 0, "aweme_details": None, "filter_list": [{"reason": reason}]},
            },
        )

    client = Client(instance, send=send, sleep=clock.sleep, monotonic=clock.mono)
    with pytest.raises(LurkerError, match="DOUYIN_DETAIL_UNAVAILABLE") as caught:
        TikHub(client).resolve_author("https://v.douyin.com/fixture/")
    assert caught.value.next_action == "provide_public_share_link"
    assert not caught.value.retryable
    assert "公开作品链接" in caught.value.message
    assert calls == [DY + "fetch_one_video_by_share_url"]
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT count(*) FROM watches").fetchone()[0] == 0
        assert db.execute("SELECT requests_reserved FROM runtime").fetchone()[0] == 1


@pytest.mark.parametrize("field", ["aweme_detail", "aweme_details"])
def test_single_work_shapes_keep_exact_author_identity(instance, clock, field):
    work = {"aweme_id": "42", "author": {"sec_uid": "author-a", "nickname": "作者"}}
    body = {"status_code": 0, field: work if field == "aweme_detail" else [work]}
    calls = []

    def send(endpoint, *args):
        calls.append(endpoint)
        return Response(
            200,
            {
                "code": 200,
                "data": {"user": work["author"]} if endpoint.endswith("handler_user_profile") else body,
            },
        )

    source = TikHub(Client(instance, send=send, sleep=clock.sleep, monotonic=clock.mono))
    author = source.resolve_author("https://v.douyin.com/fixture/")
    assert (author.author_id, author.author_name) == ("author-a", "作者")
    assert calls == [DY + "fetch_one_video_by_share_url", DY + "handler_user_profile"]
    validate_probe(DY + "fetch_one_video", body, {"aweme_id": "42"})
    with pytest.raises(LurkerError, match="PAGE_IDENTITY_INVALID"):
        validate_probe(DY + "fetch_one_video", body, {"aweme_id": "another-work"})


@pytest.mark.parametrize(
    "body",
    [
        {"aweme_details": [{"aweme_id": "1"}, {"aweme_id": "2"}]},
        {"aweme_detail": {"aweme_id": "1"}, "aweme_details": [{"aweme_id": "2"}]},
        {"aweme_details": [None]},
    ],
)
def test_ambiguous_detail_never_guesses_first_work(body):
    with pytest.raises(LurkerError, match="PAGE_IDENTITY_INVALID"):
        douyin_detail(body)


def test_missing_work_is_not_assumed_private():
    with pytest.raises(LurkerError, match="DOUYIN_DETAIL_UNAVAILABLE") as caught:
        douyin_detail({"status_code": 0, "aweme_details": [], "filter_list": [{"reason": "5"}]})
    assert "私密" not in caught.value.message
