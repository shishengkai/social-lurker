PRAGMA foreign_keys = ON;
PRAGMA application_id = 1397509425;
PRAGMA user_version = 1;

CREATE TABLE runtime (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  instance_id TEXT NOT NULL UNIQUE,
  request_count_day TEXT NOT NULL,
  requests_reserved INTEGER NOT NULL DEFAULT 0 CHECK (requests_reserved >= 0),
  api_blocked_until INTEGER,
  api_next_start_at_ms INTEGER,
  api_holds_json TEXT NOT NULL DEFAULT '[]',
  last_automatic_slot INTEGER,
  recovery_applied_slot INTEGER,
  incidents_json TEXT NOT NULL DEFAULT '[]',
  updated_at INTEGER NOT NULL
);

CREATE TABLE watches (
  id TEXT PRIMARY KEY NOT NULL,
  platform TEXT NOT NULL,
  author_id TEXT NOT NULL,
  author_name TEXT NOT NULL,
  profile_url TEXT,
  source_variant TEXT NOT NULL DEFAULT 'default',
  status TEXT NOT NULL CHECK (status IN ('active','paused','removed')),
  generation INTEGER NOT NULL DEFAULT 1 CHECK (generation > 0),
  watch_since INTEGER NOT NULL,
  discovery_floor INTEGER NOT NULL,
  initial_check_pending INTEGER NOT NULL DEFAULT 1 CHECK (initial_check_pending IN (0,1)),
  next_check_at INTEGER NOT NULL,
  last_attempt_at INTEGER,
  last_success_at INTEGER,
  last_scan_upper INTEGER,
  scan_state_json TEXT,
  coverage_state TEXT NOT NULL DEFAULT 'new'
    CHECK (coverage_state IN ('new','bounded','ok','partial','gapped','degraded')),
  coverage_gaps_json TEXT NOT NULL DEFAULT '[]',
  failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
  error_code TEXT,
  error_since INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (platform, author_id),
  UNIQUE (id, platform)
);

CREATE TABLE updates (
  id TEXT PRIMARY KEY NOT NULL,
  watch_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  work_id TEXT NOT NULL,
  author_name TEXT NOT NULL,
  published_at INTEGER,
  title TEXT NOT NULL DEFAULT '',
  source_url TEXT,
  cover_url TEXT,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  eligible_generation INTEGER NOT NULL CHECK (eligible_generation > 0),
  eligibility_lower INTEGER NOT NULL,
  eligibility_upper INTEGER NOT NULL,
  reason TEXT NOT NULL CHECK (reason IN ('new','test')),
  state TEXT NOT NULL CHECK (state IN
    ('blocked','queued','sending','sent','unknown','cancelled','ignored')),
  next_attempt_at INTEGER,
  failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
  error_code TEXT,
  attempt_id TEXT UNIQUE,
  send_started_at INTEGER,
  payload TEXT,
  payload_hash TEXT,
  provider_message_id TEXT,
  sent_at INTEGER,
  UNIQUE (platform, work_id),
  CHECK (eligibility_lower <= eligibility_upper),
  FOREIGN KEY (watch_id, platform) REFERENCES watches(id, platform) ON DELETE CASCADE,
  CHECK (state NOT IN ('queued','sending','sent','unknown') OR
    (published_at IS NOT NULL AND source_url IS NOT NULL AND length(source_url)>0)),
  CHECK (state NOT IN ('sending','sent','unknown') OR
    (attempt_id IS NOT NULL AND payload IS NOT NULL AND payload_hash IS NOT NULL)),
  CHECK (state <> 'sent' OR (provider_message_id IS NOT NULL AND sent_at IS NOT NULL))
);

CREATE INDEX watches_due ON watches(status, next_check_at);
CREATE INDEX updates_due ON updates(state, next_attempt_at, published_at);
CREATE INDEX updates_watch ON updates(watch_id, state);
