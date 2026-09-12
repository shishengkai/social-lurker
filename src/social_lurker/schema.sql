CREATE TABLE accounts (
 id INTEGER PRIMARY KEY, platform TEXT NOT NULL CHECK(platform IN ('wechat_channels','douyin')),
 platform_account_id TEXT NOT NULL, display_name TEXT NOT NULL, profile_url TEXT NOT NULL,
 tracking_state TEXT NOT NULL DEFAULT 'active' CHECK(tracking_state IN ('active','stopped','deleting')),
 watch_epoch INTEGER NOT NULL DEFAULT 1, watch_started_at REAL NOT NULL, stopped_at REAL,
 last_check_started_at REAL, last_check_completed_at REAL, coverage_until REAL, next_check_at REAL,
 last_error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(platform,platform_account_id)
);
CREATE TABLE works (
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 platform_work_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL,
 published_at REAL, first_seen_at REAL NOT NULL, availability TEXT NOT NULL DEFAULT 'available',
 caption_text TEXT, raw_transcript_text TEXT, raw_text_hash TEXT, transcript_text TEXT,
 transcript_source TEXT, text_obtained_at REAL, text_hash TEXT,
 processing_state TEXT NOT NULL DEFAULT 'pending' CHECK(processing_state IN
 ('pending','fetching','transcribing','pending_proofread','proofreading','submit_unknown','ready','no_speech','retry_wait','blocked','failed','unavailable')),
 attempt_count INTEGER NOT NULL DEFAULT 0, stage_attempts TEXT NOT NULL DEFAULT '{}',
 execution_cycle INTEGER NOT NULL DEFAULT 1, next_attempt_at REAL, external_job_id TEXT,
 asr_provider TEXT, asr_model TEXT, asr_phase TEXT, operation_token TEXT, asr_submitted_at REAL, asr_reviewed_at REAL,
 media_duration_ms INTEGER, last_error_code TEXT, last_error_message TEXT,
 owner_token TEXT, lease_until REAL, work_relpath TEXT,
 proofread_progress TEXT, proofread_rule_version TEXT, proofread_model TEXT, proofread_at REAL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(account_id,platform_work_id)
);
CREATE TABLE collection_runs (
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('initial','poll','history')), watch_epoch INTEGER NOT NULL,
 request_id TEXT UNIQUE, parent_run_id INTEGER REFERENCES collection_runs(id) ON DELETE SET NULL,
 scope_type TEXT NOT NULL CHECK(scope_type IN ('latest','time','count','all','selected')),
 requested_at REAL NOT NULL, range_start REAL, range_end REAL NOT NULL, requested_count INTEGER,
 reported_total INTEGER, total_basis TEXT, total_observed_at REAL, cursor TEXT,
 enumeration_complete INTEGER NOT NULL DEFAULT 0 CHECK(enumeration_complete IN (0,1)), last_page_at REAL,
 plan_confirmed INTEGER NOT NULL DEFAULT 1 CHECK(plan_confirmed IN (0,1)),
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN
 ('queued','running','retry_wait','blocked','completed','completed_with_errors','failed','canceled')),
 started_at REAL, finished_at REAL, attempt_count INTEGER NOT NULL DEFAULT 0,
 last_error_code TEXT, next_attempt_at REAL
);
CREATE TABLE collection_items (
 run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
 work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
 result TEXT NOT NULL DEFAULT 'pending' CHECK(result IN
 ('pending','acquired','reused','no_speech','failed','unavailable','blocked','skipped')),
 requires_notification INTEGER NOT NULL DEFAULT 0 CHECK(requires_notification IN (0,1)),
 error_code TEXT, created_at REAL NOT NULL, finished_at REAL, PRIMARY KEY(run_id,work_id)
);
CREATE TABLE notifications (
 id INTEGER PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('work','history_summary','incident')),
 work_id INTEGER REFERENCES works(id) ON DELETE CASCADE,
 run_id INTEGER REFERENCES collection_runs(id) ON DELETE CASCADE,
 account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE, watch_epoch INTEGER,
 incident_code TEXT, incident_scope TEXT, resolved_at REAL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','sent','retry_wait','unknown','canceled','blocked')),
 attempt_count INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL, cancel_reason TEXT,
 provider_message_id TEXT, dispatch_token TEXT, sent_at REAL, last_error_code TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 CHECK((kind='work' AND work_id IS NOT NULL AND run_id IS NULL AND account_id IS NOT NULL AND watch_epoch IS NOT NULL)
 OR (kind='history_summary' AND work_id IS NULL AND run_id IS NOT NULL AND account_id IS NOT NULL AND watch_epoch IS NOT NULL)
 OR (kind='incident' AND work_id IS NULL AND run_id IS NULL AND incident_code IS NOT NULL AND incident_scope IS NOT NULL))
);
CREATE INDEX works_account_time ON works(account_id,published_at);
CREATE INDEX works_due ON works(processing_state,next_attempt_at);
CREATE INDEX runs_pending ON collection_runs(account_id,state,kind);
CREATE INDEX items_work ON collection_items(work_id);
CREATE INDEX notifications_due ON notifications(state,next_attempt_at);
CREATE UNIQUE INDEX incident_active ON notifications(incident_scope,incident_code) WHERE kind='incident' AND resolved_at IS NULL;
PRAGMA user_version=1;
