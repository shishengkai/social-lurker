import pytest

from social_lurker.config import Instance
from social_lurker.media import Audio
from social_lurker.providers.tikhub import Account, Page, WorkMetadata
from social_lurker.store import Store
from social_lurker.util import now, token


@pytest.fixture
def env(tmp_path):
    instance = Instance(tmp_path, token())
    settings = instance.initialize(binding_confirmed=True)
    settings["delivery"] = {"verified": True, "max_chars": None}
    settings["execution"]["tick_soft_seconds"] = 1
    store = Store(instance.db_path)
    yield instance, store, settings
    store.close()


def author(platform="douyin", ident="author"):
    return Account(platform, ident, "测试博主", "https://www.douyin.com/user/author")


def video(ident="1", published=None):
    return WorkMetadata(
        str(ident),
        "测试作品",
        "https://www.douyin.com/video/" + str(ident),
        now() - 10 if published is None else published,
        "配文不是口播",
    )


class Social:
    def __init__(self, pages=None):
        self.pages = pages or [Page([video()], None, False)]
        self.page_calls = []
        self.detail_calls = 0
        self.on_detail = None

    def page(self, account, cursor):
        self.page_calls.append(cursor)
        return self.pages[int(cursor or 0)]

    def detail(self, account, work):
        self.detail_calls += 1
        if self.on_detail:
            self.on_detail()
        return video(work["platform_work_id"])

    def media_source(self, platform, detail):
        return {"url": "https://example.com/media.mp4", "duration": 5, "size": 16, "decode_key": None}


class FakeMedia:
    def __init__(self):
        self.calls = 0

    def prepare(self, source, directory, heartbeat):
        self.calls += 1
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "audio.mp3"
        path.write_bytes(b"fake audio")
        heartbeat()
        return Audio(path, 5000, path.stat().st_size)


class ASR:
    def __init__(self, text="完整口播。重复也要保留。"):
        self.text = text
        self.uploads = self.submits = self.queries = 0
        self.submit_error = None

    def upload(self, path, heartbeat):
        self.uploads += 1
        return "https://v3.fal.media/audio.mp3"

    def submit(self, url):
        self.submits += 1
        if self.submit_error:
            raise self.submit_error
        return "job-" + str(self.submits)

    def query(self, job):
        self.queries += 1
        return "COMPLETED"

    def fetch_result(self, job):
        return self.text
