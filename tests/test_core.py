from dataclasses import replace

import pytest
from conftest import FakeAdapter, records, work

from social_lurker.adapters.base import Author, Page
from social_lurker.db import Database
from social_lurker.errors import LurkerError
from social_lurker.output import summary
from social_lurker.service import Service


def baseline(env, ids=("old",)):
    *_, adapter, service = env
    adapter.pages = {None: Page([work(i) for i in ids], "never", True)}
    return service.add("https://www.douyin.com/user/author-a", adapter)


def test_add_only_homepage_idempotent_and_refollow(env):
    _, _, _, db, stream, _, _, adapter, service = env
    result = baseline(env, ("a", "a", "b"))
    assert result["baseline_count"] == 2 and result["changed"]
    assert adapter.requests == [("resolve", None), ("author-a", None)]
    assert stream.getvalue() == ""
    followed = db.author("douyin", "author-a")["followed_at"]
    assert not service.add("link", adapter)["changed"]
    assert db.author("douyin", "author-a")["followed_at"] == followed
    assert len(adapter.requests) == 3
    assert db.unfollow("douyin", "author-a")["changed"]
    assert not db.unfollow("douyin", "author-a")["changed"]
    adapter.pages = {None: LurkerError("REQUEST_TIMEOUT")}
    with pytest.raises(LurkerError):
        service.add("link", adapter)
    assert db.author("douyin", "author-a")["status"] == "unfollowed"
    adapter.pages = {None: Page([work("c")])}
    assert service.add("link", adapter)["baseline_count"] == 1
    assert db.authors()[0]["works_count"] == 3


def test_new_pages_then_whole_mixed_page_stop_and_old_date(env):
    _, _, _, db, stream, stats, _, adapter, service = env
    baseline(env)
    adapter.requests.clear()
    adapter.pages = {
        None: Page([work("A"), work("B")], "second", True),
        "second": Page([work("old", title="改题"), work("C")], "third", True),
    }
    assert service.check() == ("ok", 0)
    assert adapter.requests == [("author-a", None), ("author-a", "second")]
    assert [r["work"]["work_id"] for r in records(stream)] == ["A", "B", "C"]
    assert all(r["work"]["published_at"].startswith("2020") for r in records(stream))
    assert db.connection.execute("SELECT title FROM works WHERE work_id='old'").fetchone()[0] == "改题"
    assert stats["new_works"] == 3 and stats["authors_succeeded"] == 1


def test_page_duplicates_are_not_old_and_metadata_null_does_not_erase(env):
    _, _, _, db, stream, _, _, adapter, service = env
    baseline(env)
    adapter.pages = {
        None: Page([work("a"), work("a")], "next", True),
        "next": Page([work("old", title=None, date=None, url=None)]),
    }
    assert service.check() == ("ok", 0)
    assert len(records(stream)) == 1
    row = db.connection.execute("SELECT * FROM works WHERE work_id='old'").fetchone()
    assert row["title"] == "原题" and row["url"] is not None


@pytest.mark.parametrize("invalid", [work(""), work("x", aid="other"), replace(work("x"), platform="other")])
def test_identity_conflict_rejects_entire_page(env, invalid):
    _, _, _, db, stream, _, _, adapter, service = env
    baseline(env)
    adapter.pages = {None: Page([work("good"), invalid])}
    assert service.check() == ("error", 1)
    assert db.authors()[0]["works_count"] == 1
    assert records(stream)[0]["type"] == "author_error"


def test_existing_work_other_author_rejects_page(env):
    _, _, _, db, _, _, _, adapter, service = env
    baseline(env, ("belongs-to-a",))
    adapter.author = Author("douyin", "author-b", "乙")
    adapter.pages = {None: Page([work("other-new", aid="author-b"), work("belongs-to-a", aid="author-b")])}
    with pytest.raises(LurkerError, match="身份"):
        service.add("link", adapter)
    assert db.author("douyin", "author-b") is None
    assert db.authors()[0]["works_count"] == 1


@pytest.mark.parametrize(
    "pages",
    [
        {None: Page([work("A")], "x", True), "x": Page([work("B")], "x", True)},
        {None: Page([work("A")], "x", True), "x": Page([work("A")], "y", True)},
        {None: Page([work("A")], "", True)},
        {None: Page([], "x", True)},
    ],
)
def test_bad_cursor_and_repeated_page_error(env, pages):
    *_, adapter, service = env
    baseline(env)
    adapter.pages = pages
    assert service.check() == ("error", 1)


def test_page_two_failure_next_run_does_not_resume(env):
    _, _, _, db, stream, stats, _, adapter, service = env
    baseline(env)
    adapter.pages = {None: Page([work("A")], "lost-page", True), "lost-page": LurkerError("REQUEST_TIMEOUT")}
    assert service.check() == ("error", 1)
    assert db.authors()[0]["works_count"] == 2
    assert stats["authors_failed"] == 1 and stats["new_works"] == 1
    stream.seek(0)
    stream.truncate()
    stats.update(summary())
    adapter.requests.clear()
    assert service.check() == ("ok", 0)
    assert adapter.requests == [("author-a", None)]
    assert stream.getvalue() == ""  # Already seen A stops at homepage; no stored cursor.
    assert set(r[0] for r in db.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")) == {
        "meta",
        "authors",
        "works",
    }


def test_missing_metadata_still_emitted_with_quality_and_global_stop(env):
    _, _, _, db, stream, stats, _, adapter, service = env
    baseline(env)
    adapter.pages = {None: Page([work("new", title=None, date=None, url=None)])}
    adapter.enrich = lambda w: (_ for _ in ()).throw(LurkerError("REQUEST_TIMEOUT"))
    assert service.check() == ("ok", 0)
    assert records(stream)[0]["work"]["missing_fields"] == ["title", "published_at", "url"]
    stream.seek(0)
    stream.truncate()
    stats.update(summary())
    adapter.pages = {None: Page([work("valid-id", title=None)], "next", True)}
    adapter.enrich = lambda w: (_ for _ in ()).throw(LurkerError("AUTH_FAILED"))
    assert service.check() == ("error", 1)
    assert [r["type"] for r in records(stream)] == ["work", "author_error"]
    assert db.authors()[0]["works_count"] == 3


def test_two_profiles_have_independent_seen_ids(env):
    registry, _, _, db, _, _, _, adapter, service = env
    baseline(env)
    p2 = registry.create("第二空间")
    second = Database(p2.db_path, p2.profile_id)
    try:
        s2 = Service(second, {"douyin": adapter}, service.output, summary())
        s2.add("link", FakeAdapter(pages={None: Page([work("different")])}))
        adapter.pages = {None: Page([work("old")])}
        assert s2.check() == ("ok", 0)
        assert s2.stats["new_works"] == 1
        assert db.authors()[0]["works_count"] == 1
    finally:
        second.close()


@pytest.mark.parametrize("fatal", [False, True])
def test_partial_counts_and_global_unchecked(env, fatal):
    _, _, _, db, stream, stats, output, adapter, service = env
    for aid in ("a", "b", "c"):
        db.upsert_page(Author("douyin", aid, aid), [], baseline=True)

    class Many(FakeAdapter):
        def fetch_page(self, author, cursor):
            if author.author_id == "b":
                raise LurkerError("QUOTA_UNAVAILABLE" if fatal else "REQUEST_TIMEOUT")
            return Page([])

    result = Service(db, {"douyin": Many()}, output, stats).check()
    assert result == ("partial", 3)
    assert stats["authors_succeeded"] == (1 if fatal else 2)
    assert stats["authors_failed"] == 1 and stats["authors_unchecked"] == (1 if fatal else 0)
    assert records(stream)[0]["type"] == "author_error"


def test_empty_profile_and_empty_baseline_success(env):
    *_, adapter, service = env
    assert service.check() == ("ok", 0)
    assert adapter.requests == []
    assert service.add("link", adapter)["baseline_count"] == 0


def test_transaction_rolls_back_uncommitted_page(env):
    _, _, _, db, _, _, _, _, _ = env
    with pytest.raises(KeyboardInterrupt):
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta VALUES ('temporary','value')")
            raise KeyboardInterrupt
    assert db.connection.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
