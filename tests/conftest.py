import io
import json
from pathlib import Path

import pytest

from social_lurker.adapters.base import Author, Page, Work
from social_lurker.config import FileSecrets
from social_lurker.db import Database
from social_lurker.output import Output, summary
from social_lurker.profiles import FileRegistry
from social_lurker.service import Service

ROOT = Path(__file__).resolve().parents[1]
from install import install  # noqa: E402


class FakeAdapter:
    def __init__(self, author=None, pages=None, enrich=None):
        self.author = author or Author("douyin", "author-a", "甲")
        self.pages = pages or {None: Page([])}
        self.requests = []
        self.enrich = enrich

    def resolve_author(self, link):
        self.requests.append(("resolve", None))
        return self.author

    def fetch_page(self, author, cursor):
        self.requests.append((author.author_id, cursor))
        value = self.pages[cursor]
        if isinstance(value, Exception):
            raise value
        return value

    def enrich_metadata(self, author, work):
        return self.enrich(work) if self.enrich else work


def work(
    wid, *, aid="author-a", title="原题", date="2020-01-01T00:00:00Z", url="https://www.douyin.com/video/1"
):
    return Work("douyin", wid, aid, title, date, url, kind="video")


@pytest.fixture
def env(tmp_path):
    registry = FileRegistry(tmp_path / "data")
    profile = registry.create("p0001", "甲的空间")
    secrets = FileSecrets(tmp_path / "credentials.json", {"TIKHUB_API_KEY": "fixture-only"})
    db = Database(profile.db_path, profile.profile_id)
    stream, stats = io.StringIO(), summary()
    output = Output("check", profile.profile_id, stream=stream)
    adapter = FakeAdapter()
    service = Service(db, {"douyin": adapter}, output, stats)
    yield registry, profile, secrets, db, stream, stats, output, adapter, service
    output.close()
    db.close()


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.fixture
def installer():
    return install
