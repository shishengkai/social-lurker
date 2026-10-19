import sqlite3

import pytest
from test_upgrade import Crash, make_package

from install import install
from social_lurker.adapters.base import Author, Work
from social_lurker.db import Database
from social_lurker.errors import LurkerError
from social_lurker.profiles import FileRegistry
from social_lurker.upgrade import apply_package, current, recover
from social_lurker.util import digest
from tools.build_release import ROOT, build


def snapshot(path):
    with sqlite3.connect(path) as db:
        return {
            table: db.execute("SELECT * FROM " + table + " ORDER BY 1,2").fetchall()
            for table in ("meta", "authors", "works")
        }


@pytest.mark.parametrize("stage", [None, "prepared", "migrated:学习", "pointer_switched"])
def test_schema1_platform_expansion_preserves_records_and_rollback(tmp_path, stage):
    old, _ = make_package(tmp_path / "old", version="0.5.4", schema=1)
    root, data = tmp_path / "install", tmp_path / "data"
    install(old, root, data)
    registry = FileRegistry(data)
    for pid in ("学习", "bot/唯一"):
        p = registry.create(pid)
        db = Database(p.db_path, pid, supported_schema=1)
        a = Author("douyin", "old-author", "旧作者")
        db.upsert_page(a, [Work("douyin", "old-work", a.author_id, "旧作品")], baseline=True)
        db.unfollow("douyin", a.author_id)
        db.close()
    before = {p.profile_id: snapshot(p.db_path) for p in registry.list()}
    registry_before = registry.path.read_bytes()
    key = tmp_path / "credentials.json"
    key.write_text("fixture-only")
    secret = digest(key)
    target = build(ROOT, tmp_path / "new", development=True)

    def fault(at):
        if at == stage:
            raise Crash()

    if stage:
        with pytest.raises(Crash):
            apply_package(root, target, fault=fault)
        recover(root)
    else:
        apply_package(root, target)
    crossed = stage in (None, "pointer_switched")
    assert current(root)[0]["version"] == ("0.6.0" if crossed else "0.5.4")
    assert registry_before == registry.path.read_bytes() and secret == digest(key)
    for p in registry.list():
        assert before[p.profile_id] == snapshot(p.db_path)
        db = Database(p.db_path, p.profile_id, create=False, supported_schema=2 if crossed else 1)
        if crossed:
            for platform in ("wechat_mp", "bilibili", "youtube"):
                a = Author(platform, "new-author", "新作者")
                db.upsert_page(a, [Work(platform, "new-work", a.author_id)], baseline=True)
            assert len(db.authors()) == 3
        else:
            with pytest.raises(sqlite3.IntegrityError):
                db.upsert_page(Author("youtube", "new-author", "新作者"), [], baseline=True)
        assert db.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.connection.execute("PRAGMA foreign_key_check").fetchall()
        db.close()
    assert list((root / "backups").glob("*/completed.json"))
    assert (root / "versions/0.5.4").is_dir()
    assert not (root / "upgrade-state.json").exists()


def test_legacy_business_db_requires_explicit_upgrade(tmp_path):
    p = tmp_path / "state.sqlite"
    d = Database(p, "old", supported_schema=1)
    d.close()
    before = p.read_bytes()
    with pytest.raises(LurkerError) as e:
        Database(p, "old")
    assert e.value.code == "DB_ERROR" and p.read_bytes() == before
