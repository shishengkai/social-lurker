from copy import deepcopy

import pytest
from conftest import ingest

from social_lurker.errors import LurkerError
from social_lurker.monitor import Monitor
from social_lurker.sources import DY, TikHub, douyin_page, validate_probe


def work(watch, ident, published_at, *, collaborator=False):
    item = {
        "aweme_id": ident,
        "author": {"sec_uid": "other-primary" if collaborator else watch["author_id"], "nickname": "作者"},
        "create_time": published_at,
        "desc": ident,
        "share_url": "https://www.douyin.com/video/" + ident,
    }
    if collaborator:
        item["cooperation_info"] = {"co_creators": [{"sec_uid": watch["author_id"]}]}
    return item


def body(watch, items, *, more=True):
    return {"sec_uid": watch["author_id"], "aweme_list": items, "has_more": int(more), "max_cursor": "123"}


class Client:
    def __init__(self, data):
        self.data, self.calls = data, []

    def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return deepcopy(self.data)


def test_owned_new_posts_survive_collaborations_but_do_not_authorize_next_page(instance, watch, clock):
    ingest(instance, watch, [])
    clock.advance(60)
    client = Client(
        body(watch, [work(watch, "1", clock.now - 1), work(watch, "2", clock.now, collaborator=True)])
    )
    result = Monitor(instance, TikHub(client), monotonic=clock.mono).poll(watch_id=watch["id"])
    assert result["pages"] == 1 and result["errors"] == [] and len(client.calls) == 1
    assert result["scans"][0]["excluded_collaborations"] == 1
    assert result["scans"][0]["stop_reason"] == "not_all_new"
    with instance.transaction("read") as (db, _):
        assert [tuple(r) for r in db.execute("SELECT work_id,state FROM updates")] == [("1", "queued")]
        row = db.execute("SELECT * FROM watches").fetchone()
        assert row["coverage_state"] == "bounded" and row["scan_state_json"] is None


def test_latest_selects_primary_author_even_when_collaboration_is_newer(instance, watch, clock):
    data = body(watch, [work(watch, "2", clock.now, collaborator=True), work(watch, "1", clock.now - 60)])
    row = Monitor(instance, TikHub(Client(data))).test_latest(watch["id"])
    assert row["work_id"] == "1" and row["state"] == "queued"
    assert row["author_name"] == "作者"


def test_only_collaborations_is_a_valid_bounded_page_without_fake_end(instance, watch, clock):
    data = body(watch, [work(watch, "2", clock.now, collaborator=True)])
    client = Client(data)
    source = TikHub(client)
    page = source.list_publications(watch)
    assert page.items == [] and page.excluded_count == 1 and page.end_state == "more"
    result = Monitor(instance, source, monotonic=clock.mono).poll(watch_id=watch["id"])
    assert result["pages"] == 1 and not result["errors"]
    assert result["scans"][0]["stop_reason"] == "initial_one_page"
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT COUNT(*) FROM updates").fetchone()[0] == 0
        assert db.execute("SELECT initial_check_pending FROM watches").fetchone()[0] == 0
    with pytest.raises(LurkerError, match="NO_AVAILABLE_PUBLICATION"):
        Monitor(instance, source).test_latest(watch["id"])
    validate_probe(DY + "fetch_user_post_videos", data, {"sec_user_id": watch["author_id"]})


@pytest.mark.parametrize(
    "cooperation", [None, {}, {"co_creators": []}, {"co_creators": [{"sec_uid": "other"}]}]
)
def test_unrecognized_foreign_author_still_rejects_entire_page(instance, watch, clock, cooperation):
    foreign = work(watch, "2", clock.now, collaborator=True)
    foreign["cooperation_info"] = cooperation
    data = body(watch, [work(watch, "1", clock.now), foreign])
    result = Monitor(instance, TikHub(Client(data)), monotonic=clock.mono).poll(watch_id=watch["id"])
    assert result["pages"] == 0 and result["errors"][0]["code"] == "PAGE_IDENTITY_INVALID"
    with instance.transaction("read") as (db, _):
        assert db.execute("SELECT COUNT(*) FROM updates").fetchone()[0] == 0
        assert db.execute("SELECT initial_check_pending FROM watches").fetchone()[0] == 1
    with pytest.raises(LurkerError, match="PAGE_IDENTITY_INVALID"):
        validate_probe(DY + "fetch_user_post_videos", data, {"sec_user_id": watch["author_id"]})


@pytest.mark.parametrize(
    "bad_case", ["root", "missing_primary", "conflicting_work", "missing_cursor", "empty_more"]
)
def test_filtering_never_masks_identity_or_page_protocol_errors(watch, clock, bad_case):
    data = body(watch, [work(watch, "1", clock.now), work(watch, "2", clock.now, collaborator=True)])
    if bad_case == "root":
        data["sec_uid"] = "wrong-author"
    elif bad_case == "missing_primary":
        data["aweme_list"][1]["author"].pop("sec_uid")
    elif bad_case == "conflicting_work":
        data["aweme_list"][1]["aweme_id"] = "1"
    elif bad_case == "missing_cursor":
        data.pop("max_cursor")
    else:
        data["aweme_list"] = []
    with pytest.raises(LurkerError):
        douyin_page(data, watch["author_id"])
    with pytest.raises(LurkerError):
        validate_probe(DY + "fetch_user_post_videos", data, {"sec_user_id": watch["author_id"]})
