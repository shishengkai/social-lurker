CREATE TABLE authors_next (platform TEXT NOT NULL CHECK (platform IN ('douyin','wechat_channels','wechat_mp','bilibili','youtube')),
 author_id TEXT NOT NULL, author_name TEXT NOT NULL, profile_url TEXT,
 status TEXT NOT NULL CHECK (status IN ('active','unfollowed')),
 created_at TEXT NOT NULL, followed_at TEXT NOT NULL, unfollowed_at TEXT,
 last_check_at TEXT, last_success_at TEXT, last_error_code TEXT,
 PRIMARY KEY(platform,author_id)
);
INSERT INTO authors_next SELECT * FROM authors;
CREATE TABLE works_next (platform TEXT NOT NULL, work_id TEXT NOT NULL, author_id TEXT NOT NULL,
 title TEXT, published_at TEXT, url TEXT, cover_url TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('video','image','text','unknown')),
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 PRIMARY KEY(platform,work_id),
 FOREIGN KEY(platform,author_id) REFERENCES authors_next(platform,author_id) ON DELETE RESTRICT
);
INSERT INTO works_next SELECT * FROM works;
DROP TABLE works;
DROP TABLE authors;
ALTER TABLE authors_next RENAME TO authors;
ALTER TABLE works_next RENAME TO works;
CREATE INDEX authors_active ON authors(status,platform,author_id);
CREATE INDEX works_author ON works(platform,author_id);
