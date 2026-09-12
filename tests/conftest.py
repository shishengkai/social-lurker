from datetime import datetime

import pytest

from social_lurker.config import Instance
from social_lurker.sources import Author, Page, Publication
from social_lurker.store import Store
from social_lurker.util import atomic_json


def timestamp(local):
    return int(datetime.fromisoformat("2026-09-13T" + local + ":00+08:00").timestamp())


class Clock:
    def __init__(self):
        self.now = timestamp("08:00")
        self.elapsed = 0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        self.elapsed += seconds

    def sleep(self, seconds):
        self.advance(seconds)

    def mono(self):
        return self.elapsed


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def instance(tmp_path, clock):
    value = Instance(str(tmp_path / "bot"), clock=clock)
    value.initialize(bot_id="fixture-bot")
    settings = value.load()
    settings["host"].update(
        delivery_verified_at=clock.now, quiet_execution_verified_at=clock.now, routine_id="fixture-routine"
    )
    settings["host"]["evidence_ref"] = {
        "schema": 1,
        "host": {
            "max_message_length": 4000,
            "length_unit": "unicode",
            "length_evidence": "fixture-length-contract",
            "durable_directory": True,
            "instance_isolated": True,
            "native_schedule_verified": True,
            "images_verified": True,
            "routine_active": True,
        },
        "sources": {},
    }
    atomic_json(value.path("settings.json"), settings)
    value.path(".env").write_text("TIKHUB_API_KEY=fixture-only-not-a-secret\n")
    return value


@pytest.fixture
def watch(instance):
    return Store(instance).add(Author("douyin", "author-a", "作者甲", variant="normal"))


def publication(w, work_id, published, **kwargs):
    return Publication(
        w["platform"],
        str(work_id),
        w["author_id"],
        w["author_name"],
        published,
        kwargs.pop("title", "一个标题"),
        kwargs.pop("source_url", "https://www.douyin.com/video/" + str(work_id)),
        **kwargs,
    )


def ingest(instance, w, items, *, cursor=None):
    store = Store(instance)
    with instance.transaction() as (db, settings):
        row = dict(db.execute("SELECT * FROM watches WHERE id=?", (w["id"],)).fetchone())
        scan = store.begin_scan(db, row, settings, int(instance.clock()))
    more = store.page(
        w["id"], row["generation"], scan, Page(items, cursor, "more" if cursor else "confirmed_end")
    )
    return more


class Source:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def capability(self, *args):
        return "recent_pages_verified"

    def list_endpoint(self, watch):
        return "/douyin/app/v3/fetch_user_post_videos"

    def list_publications(self, watch, cursor=None, **kwargs):
        self.calls.append((watch["id"], cursor))
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def complete_metadata(self, watch, row, **kwargs):
        return self.pages.pop(0)
