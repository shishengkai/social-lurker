import json

import pytest

from social_lurker.errors import LurkerError
from social_lurker.http import Client, Response
from social_lurker.sources import WX, Author, TikHub, validate_probe
from social_lurker.store import Store


def test_provider_error_is_not_an_empty_page_or_identity_conflict(instance, clock):
    watch = Store(instance).add(Author("wechat_channels", "fixture@finder", "作者"))
    calls = []
    data = {"message": "private provider diagnostic", "debug_info": "do not expose", "debug_id": "fixture"}
    client = Client(
        instance,
        send=lambda *args: calls.append(args) or Response(200, {"code": 200, "data": data}),
        sleep=clock.sleep,
        monotonic=clock.mono,
    )
    with pytest.raises(LurkerError, match="WECHAT_LIST_UNAVAILABLE") as caught:
        TikHub(client).list_publications(watch)
    assert len(calls) == 1 and calls[0][1]["raw"] is False
    assert caught.value.retryable is False
    assert caught.value.next_action == "verify_author_and_endpoint"
    assert "private provider" not in json.dumps(caught.value.public())
    assert "debug_info" not in json.dumps(caught.value.public())
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT COUNT(*) FROM updates").fetchone()[0] == 0
        assert db.execute("SELECT requests_reserved FROM runtime").fetchone()[0] == 1
    with pytest.raises(LurkerError, match="WECHAT_LIST_UNAVAILABLE"):
        validate_probe(WX + "fetch_user_videos", data, {"username": watch["author_id"]})


def test_provider_message_does_not_mask_real_identity_conflict(instance, clock):
    watch = Store(instance).add(Author("wechat_channels", "fixture@finder", "作者"))
    data = {"message": "ok", "username": "other@finder", "videos": [], "count": 0, "up_continue": 0}
    client = Client(
        instance,
        send=lambda *a: Response(200, {"code": 200, "data": data}),
        sleep=clock.sleep,
        monotonic=clock.mono,
    )
    with pytest.raises(LurkerError, match="PAGE_IDENTITY_INVALID"):
        TikHub(client).list_publications(watch)


def test_valid_empty_page_with_message_still_confirms_end(instance, clock):
    watch = Store(instance).add(Author("wechat_channels", "fixture@finder", "作者"))
    data = {"message": "ok", "username": watch["author_id"], "videos": [], "count": 0, "up_continue": 0}
    client = Client(
        instance,
        send=lambda *a: Response(200, {"code": 200, "data": data}),
        sleep=clock.sleep,
        monotonic=clock.mono,
    )
    page = TikHub(client).list_publications(watch)
    assert page.end_state == "confirmed_end" and page.items == []
    validate_probe(WX + "fetch_user_videos", data, {"username": watch["author_id"]})
