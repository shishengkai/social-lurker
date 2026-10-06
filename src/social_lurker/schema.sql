CREATE TABLE meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);
CREATE TABLE authors (
 platform TEXT NOT NULL CHECK (platform IN ('douyin','wechat_channels')),
 author_id TEXT NOT NULL, author_name TEXT NOT NULL, profile_url TEXT,
 status TEXT NOT NULL CHECK (status IN ('active','unfollowed')),
 created_at TEXT NOT NULL, followed_at TEXT NOT NULL, unfollowed_at TEXT,
 last_check_at TEXT, last_success_at TEXT, last_error_code TEXT,
 PRIMARY KEY(platform,author_id)
);
CREATE TABLE works (
 platform TEXT NOT NULL, work_id TEXT NOT NULL, author_id TEXT NOT NULL,
 title TEXT, published_at TEXT, url TEXT, cover_url TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('video','image','text','unknown')),
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 PRIMARY KEY(platform,work_id),
 FOREIGN KEY(platform,author_id) REFERENCES authors(platform,author_id) ON DELETE RESTRICT
);
CREATE INDEX authors_active ON authors(status,platform,author_id);
CREATE INDEX works_author ON works(platform,author_id);
