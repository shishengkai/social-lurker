import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import LurkerError, require
from .util import now

APPLICATION_ID = 0x534C3530
SCHEMA_VERSION = 1


class Database:
    def __init__(self, path, profile_id, *, create=True, supported_schema=SCHEMA_VERSION):
        self.path = Path(path)
        require(self.path.is_absolute(), "DB_ERROR")
        existed = self.path.exists()
        require(create or existed, "DB_ERROR")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = None
        try:
            self.connection = sqlite3.connect(self.path, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA busy_timeout=5000")
            if not existed:
                self.connection.execute("PRAGMA journal_mode=DELETE")
                self.connection.execute("PRAGMA synchronous=FULL")
                # DDL and identity are one atomic initialization; existing empty files are rejected.
                sql = Path(__file__).with_name("schema.sql").read_text()
                self.connection.executescript("BEGIN IMMEDIATE;" + sql)
                self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self.connection.execute("INSERT INTO meta VALUES ('profile_id',?)", (profile_id,))
                self.connection.commit()
            self.validate(profile_id, supported_schema)
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=FULL")
        except (sqlite3.Error, OSError, LurkerError, TypeError, IndexError):
            if self.connection:
                self.connection.close()
            raise LurkerError("DB_ERROR") from None

    def validate(self, profile_id, supported_schema=SCHEMA_VERSION):
        db = self.connection
        require(db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID, "DB_ERROR")
        require(db.execute("PRAGMA user_version").fetchone()[0] == supported_schema, "DB_ERROR")
        require(
            db.execute("SELECT value FROM meta WHERE key='profile_id'").fetchone()[0] == profile_id,
            "DB_ERROR",
        )
        require(dict(db.execute("SELECT key,value FROM meta")) == {"profile_id": profile_id}, "DB_ERROR")
        require(
            {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            == {"meta", "authors", "works"},
            "DB_ERROR",
        )
        # Access every required column before network operations.
        db.execute(
            "SELECT platform,author_id,author_name,profile_url,status,created_at,followed_at,"
            "unfollowed_at,last_check_at,last_success_at,last_error_code FROM authors LIMIT 0"
        )
        db.execute(
            "SELECT platform,work_id,author_id,title,published_at,url,cover_url,kind,"
            "first_seen_at,last_seen_at FROM works LIMIT 0"
        )
        require(
            db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and not db.execute("PRAGMA foreign_key_check").fetchall(),
            "DB_ERROR",
        )

    @contextmanager
    def transaction(self):
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def close(self):
        self.connection.close()

    def authors(self, all_authors=False):
        sql = "SELECT a.*,COUNT(w.work_id) AS works_count FROM authors a LEFT JOIN works w "
        sql += "ON a.platform=w.platform AND a.author_id=w.author_id "
        if not all_authors:
            sql += "WHERE a.status='active' "
        sql += "GROUP BY a.platform,a.author_id ORDER BY a.platform,a.author_id"
        return [dict(row) for row in self.connection.execute(sql)]

    def author(self, platform, author_id):
        row = self.connection.execute(
            "SELECT * FROM authors WHERE platform=? AND author_id=?", (platform, author_id)
        ).fetchone()
        return dict(row) if row else None

    def known(self, author, items):
        result = set()
        for item in items:
            row = self.connection.execute(
                "SELECT author_id FROM works WHERE platform=? AND work_id=?", (author.platform, item.work_id)
            ).fetchone()
            if row:
                require(row[0] == author.author_id, "PAGE_IDENTITY_INVALID")
                result.add(item.work_id)
        return result

    def upsert_page(self, author, items, *, baseline=False):
        at = now()
        with self.transaction() as db:
            if baseline:
                db.execute(
                    "INSERT INTO authors (platform,author_id,author_name,profile_url,status,"
                    "created_at,followed_at) VALUES (?,?,?,?,'active',?,?) "
                    "ON CONFLICT(platform,author_id) DO UPDATE SET author_name=excluded.author_name,"
                    "profile_url=COALESCE(excluded.profile_url,authors.profile_url),status='active',"
                    "followed_at=excluded.followed_at,unfollowed_at=NULL",
                    (author.platform, author.author_id, author.name, author.profile_url, at, at),
                )
            for item in items:
                db.execute(
                    "INSERT INTO works (platform,work_id,author_id,title,published_at,url,cover_url,kind,"
                    "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(platform,work_id) DO UPDATE SET "
                    "title=COALESCE(excluded.title,works.title),"
                    "published_at=COALESCE(excluded.published_at,works.published_at),"
                    "url=COALESCE(excluded.url,works.url),cover_url=COALESCE(excluded.cover_url,works.cover_url),"
                    "kind=CASE WHEN excluded.kind='unknown' THEN works.kind ELSE excluded.kind END,"
                    "last_seen_at=excluded.last_seen_at",
                    (
                        item.platform,
                        item.work_id,
                        item.author_id,
                        item.title,
                        item.published_at,
                        item.url,
                        item.cover_url,
                        item.kind,
                        at,
                        at,
                    ),
                )
        return at

    def mark(self, author, phase, code=None):
        with self.transaction() as db:
            if phase == "start":
                db.execute(
                    "UPDATE authors SET last_check_at=? WHERE platform=? AND author_id=?",
                    (now(), author.platform, author.author_id),
                )
            elif phase == "success":
                db.execute(
                    "UPDATE authors SET last_success_at=?,last_error_code=NULL "
                    "WHERE platform=? AND author_id=?",
                    (now(), author.platform, author.author_id),
                )
            else:
                db.execute(
                    "UPDATE authors SET last_error_code=? WHERE platform=? AND author_id=?",
                    (code, author.platform, author.author_id),
                )

    def unfollow(self, platform, author_id):
        row = self.author(platform, author_id)
        require(row is not None, "AUTHOR_NOT_FOUND")
        changed = row["status"] != "unfollowed"
        if changed:
            with self.transaction() as db:
                db.execute(
                    "UPDATE authors SET status='unfollowed',unfollowed_at=? WHERE platform=? AND author_id=?",
                    (now(), platform, author_id),
                )
        return {"platform": platform, "author_id": author_id, "status": "unfollowed", "changed": changed}
