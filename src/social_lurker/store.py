import contextlib
import json
import random
import sqlite3
from pathlib import Path

from .errors import require
from .util import digest, json_text, now, token

OPEN_RUNS = "('queued','running','retry_wait','blocked')"
ACTIVE_RUNS = "('running','retry_wait','blocked')"
TERMINAL_ITEMS = {"acquired", "reused", "no_speech", "failed", "unavailable", "skipped"}


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        require(version in (0, 1), "SCHEMA_UNSUPPORTED", "数据库版本不兼容，需受控迁移")
        if version == 0:
            self.conn.executescript(
                "BEGIN IMMEDIATE;\n" + Path(__file__).with_name("schema.sql").read_text() + "\nCOMMIT;"
            )
        self.path.chmod(0o600)

    def close(self):
        self.conn.close()

    @contextlib.contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def execute(self, sql, values=()):
        return self.conn.execute(sql, values)

    def one(self, sql, values=()):
        row = self.execute(sql, values).fetchone()
        return dict(row) if row else None

    def all(self, sql, values=()):
        return [dict(r) for r in self.execute(sql, values).fetchall()]

    def account(self, account_id):
        row = self.one("SELECT * FROM accounts WHERE id=?", (account_id,))
        require(row, "NOT_FOUND", "账号不存在")
        return row

    def work(self, work_id):
        row = self.one("SELECT * FROM works WHERE id=?", (work_id,))
        require(row, "NOT_FOUND", "作品不存在")
        return row

    def run(self, run_id):
        row = self.one("SELECT * FROM collection_runs WHERE id=?", (run_id,))
        require(row, "NOT_FOUND", "收集任务不存在")
        return row

    def create_run(
        self,
        account,
        kind,
        request_id=None,
        scope="time",
        start=None,
        end=None,
        count=None,
        confirmed=True,
        parent=None,
    ):
        t = now()
        cursor = self.execute(
            """INSERT INTO collection_runs
          (account_id,kind,watch_epoch,request_id,parent_run_id,scope_type,requested_at,range_start,range_end,
           requested_count,plan_confirmed) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                account["id"],
                kind,
                account["watch_epoch"],
                request_id,
                parent,
                scope,
                t,
                start,
                t if end is None else end,
                count,
                int(confirmed),
            ),
        )
        return self.run(cursor.lastrowid)

    def add_account(self, metadata, request_id):
        with self.tx():
            previous = self.one(
                "SELECT * FROM accounts WHERE platform=? AND platform_account_id=?",
                (metadata.platform, metadata.id),
            )
            if previous:
                return {
                    "account_id": previous["id"],
                    "created": False,
                    "tracking_state": previous["tracking_state"],
                }
            t = now()
            aid = self.execute(
                """INSERT INTO accounts(platform,platform_account_id,display_name,profile_url,
              watch_started_at,next_check_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                (metadata.platform, metadata.id, metadata.name, metadata.profile_url, t, t, t, t),
            ).lastrowid
            run = self.create_run(self.account(aid), "initial", request_id, "latest", end=t, count=1)
            return {
                "account_id": aid,
                "initial_run_id": run["id"],
                "created": True,
                "history_options": ["none", "time", "count", "all"],
            }

    def control(self, account_id, action, expected_epoch=None):
        with self.tx():
            account = self.account(account_id)
            if expected_epoch is not None and account["watch_epoch"] != expected_epoch:
                return account  # Replay after durable intent: mutation already applied; never apply twice.
            t = now()
            if action in ("stop", "delete"):
                target = "deleting" if action == "delete" else "stopped"
                if account["tracking_state"] == "deleting" or account["tracking_state"] == target:
                    return account
                self.execute(
                    """UPDATE accounts SET tracking_state=?,watch_epoch=watch_epoch+1,
                  stopped_at=?,updated_at=? WHERE id=?""",
                    (target, t, t, account_id),
                )
                self.execute(
                    "UPDATE collection_runs SET state='canceled',finished_at=? WHERE account_id=? AND state='queued'",
                    (t, account_id),
                )
                # sending may already have crossed the external boundary; preserve unknown for reconciliation.
                self.execute(
                    """UPDATE notifications SET state=CASE WHEN state='sending' THEN 'unknown' ELSE 'canceled' END,
                   cancel_reason='ACCOUNT_STOPPED',updated_at=? WHERE account_id=? AND state IN
                   ('pending','retry_wait','sending','blocked')""",
                    (t, account_id),
                )
            elif action == "resume":
                require(account["tracking_state"] != "deleting", "DELETING", "删除中不能恢复")
                if account["tracking_state"] == "stopped":
                    self.execute(
                        """UPDATE accounts SET tracking_state='active',watch_epoch=watch_epoch+1,
                      watch_started_at=?,coverage_until=NULL,next_check_at=?,updated_at=? WHERE id=?""",
                        (t, t, t, account_id),
                    )
            return self.account(account_id)

    def discover(self, run, item):
        # Caller owns transaction: page entries and cursor advance must commit together.
        t = now()
        self.execute(
            """INSERT INTO works(account_id,platform_work_id,title,source_url,published_at,first_seen_at,
          caption_text,created_at,updated_at,last_error_code) VALUES(?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(account_id,platform_work_id) DO UPDATE SET title=excluded.title,
          source_url=CASE WHEN excluded.source_url='' THEN works.source_url ELSE excluded.source_url END,published_at=COALESCE(excluded.published_at,works.published_at),
          caption_text=excluded.caption_text""",
            (
                run["account_id"],
                item.id,
                item.title,
                item.source_url,
                item.published_at,
                t,
                item.caption,
                t,
                t,
                None if item.published_at is not None else "PUBLISHED_AT_UNKNOWN",
            ),
        )
        work = self.one(
            "SELECT * FROM works WHERE account_id=? AND platform_work_id=?", (run["account_id"], item.id)
        )
        result = {
            "ready": "reused",
            "no_speech": "no_speech",
            "failed": "failed",
            "unavailable": "unavailable",
            "blocked": "blocked",
            "submit_unknown": "blocked",
        }.get(work["processing_state"], "pending")
        self.execute(
            """INSERT OR IGNORE INTO collection_items(run_id,work_id,result,created_at,finished_at)
          VALUES(?,?,?,?,?)""",
            (run["id"], work["id"], result, t, t if result in TERMINAL_ITEMS else None),
        )

    def notify_work(self, work_id):
        row = self.one(
            """SELECT a.id,a.watch_epoch FROM accounts a JOIN works w ON w.account_id=a.id
          JOIN collection_items i ON i.work_id=w.id JOIN collection_runs r ON r.id=i.run_id
          WHERE w.id=? AND w.processing_state IN ('ready','no_speech') AND i.requires_notification=1
          AND r.enumeration_complete=1 AND r.watch_epoch=a.watch_epoch AND a.tracking_state='active'
          AND r.state!='canceled' LIMIT 1""",
            (work_id,),
        )
        if row:
            t = now()
            self.execute(
                """INSERT OR IGNORE INTO notifications(dedupe_key,kind,work_id,account_id,watch_epoch,
              created_at,updated_at) VALUES(?,'work',?,?,?,?,?)""",
                (f"work:{work_id}", work_id, row["id"], row["watch_epoch"], t, t),
            )

    def finish_work(
        self, work_id, state, *, owner=None, raw=None, final=None, source=None, duration=None, model=None
    ):
        with self.tx():
            work = self.work(work_id)
            if owner:
                require(work["owner_token"] == owner, "STALE_OWNER", "执行权已变更")
            t = now()
            if raw is not None:
                self.execute(
                    """UPDATE works SET raw_transcript_text=?,raw_text_hash=?,transcript_source=?,
                  text_obtained_at=?,media_duration_ms=COALESCE(?,media_duration_ms),asr_phase='stored'
                  WHERE id=?""",
                    (raw, digest(raw), source, t, duration, work_id),
                )
            if final is not None:
                self.execute(
                    """UPDATE works SET transcript_text=?,text_hash=?,proofread_progress=NULL,
                  proofread_rule_version='1',proofread_model=?,proofread_at=? WHERE id=?""",
                    (final, digest(final), model, t, work_id),
                )
            self.execute(
                """UPDATE works SET processing_state=?,next_attempt_at=NULL,last_error_code=NULL,
              last_error_message=NULL,owner_token=NULL,lease_until=NULL,updated_at=? WHERE id=?""",
                (state, t, work_id),
            )
            result = {"ready": "acquired", "no_speech": "no_speech"}.get(state)
            if result:
                self.execute(
                    f"""UPDATE collection_items SET result=?,finished_at=?,error_code=NULL
                  WHERE work_id=? AND result IN ('pending','blocked') AND run_id IN
                  (SELECT id FROM collection_runs WHERE state IN {OPEN_RUNS})""",
                    (result, t, work_id),
                )
                self.notify_work(work_id)
        self.finish_runs()

    def finish_runs(self):
        with self.tx():
            for run in self.all(
                f"SELECT * FROM collection_runs WHERE state IN {ACTIVE_RUNS} AND enumeration_complete=1 AND plan_confirmed=1"
            ):
                counts = self.counts(run["id"])
                if counts.get("pending", 0):
                    self.execute("UPDATE collection_runs SET state='running' WHERE id=?", (run["id"],))
                    continue
                if counts.get("blocked", 0):
                    self.execute("UPDATE collection_runs SET state='blocked' WHERE id=?", (run["id"],))
                    continue
                t = now()
                state = (
                    "completed_with_errors"
                    if counts.get("failed", 0) or counts.get("unavailable", 0)
                    else "completed"
                )
                self.execute(
                    "UPDATE collection_runs SET state=?,finished_at=? WHERE id=?", (state, t, run["id"])
                )
                account = self.account(run["account_id"])
                if (
                    run["kind"] == "history"
                    and account["tracking_state"] == "active"
                    and account["watch_epoch"] == run["watch_epoch"]
                ):
                    self.execute(
                        """INSERT OR IGNORE INTO notifications(dedupe_key,kind,run_id,account_id,watch_epoch,
                      created_at,updated_at) VALUES(?,'history_summary',?,?,?,?,?)""",
                        (f"history:{run['id']}", run["id"], account["id"], run["watch_epoch"], t, t),
                    )

    def counts(self, run_id):
        return {
            r["result"]: r["n"]
            for r in self.all(
                "SELECT result,count(*) AS n FROM collection_items WHERE run_id=? GROUP BY result", (run_id,)
            )
        }

    def incident(self, code, scope, account_id=None):
        account = self.account(account_id) if account_id else None
        if account and account["tracking_state"] != "active":
            return
        if not account and not self.one("SELECT id FROM accounts WHERE tracking_state='active' LIMIT 1"):
            return
        t = now()
        self.execute(
            """INSERT OR IGNORE INTO notifications(dedupe_key,kind,account_id,watch_epoch,
          incident_code,incident_scope,created_at,updated_at) VALUES(?,'incident',?,?,?,?,?,?)""",
            (
                f"incident:{token()}",
                account_id,
                account["watch_epoch"] if account else None,
                code,
                scope,
                t,
                t,
            ),
        )

    def resolve_incident(self, code, scope):
        self.execute(
            """UPDATE notifications SET resolved_at=?,state=CASE WHEN state IN ('pending','retry_wait','blocked')
          THEN 'canceled' ELSE state END WHERE incident_code=? AND incident_scope=? AND resolved_at IS NULL""",
            (now(), code, scope),
        )

    def fail_work(self, work_id, stage, error, settings):
        with self.tx():
            work = self.work(work_id)
            attempts = json.loads(work["stage_attempts"])
            attempts[stage] = attempts.get(stage, 0) + 1
            state = (
                "retry_wait"
                if error.retryable and attempts[stage] < 3
                else ("failed" if error.retryable else "blocked")
            )
            if error.code == "UNAVAILABLE":
                state = "unavailable"
            delay = settings["processing"]["retry_delays_seconds"][
                min(attempts[stage] - 1, 1)
            ] + random.uniform(0, 10)
            due = now() + max(delay, error.retry_after or 0) if state == "retry_wait" else None
            self.execute(
                """UPDATE works SET processing_state=?,stage_attempts=?,attempt_count=attempt_count+1,
              next_attempt_at=?,last_error_code=?,last_error_message=?,owner_token=NULL,lease_until=NULL,updated_at=? WHERE id=?""",
                (state, json_text(attempts), due, error.code, error.message, now(), work_id),
            )
            result = {"blocked": "blocked", "failed": "failed", "unavailable": "unavailable"}.get(
                state, "pending"
            )
            self.execute(
                f"""UPDATE collection_items SET result=?,error_code=?,finished_at=? WHERE work_id=?
              AND result IN ('pending','blocked') AND run_id IN (SELECT id FROM collection_runs WHERE state IN {OPEN_RUNS})""",
                (result, error.code, now() if result in TERMINAL_ITEMS else None, work_id),
            )
            if state in ("blocked", "failed"):
                self.incident(error.code, f"account:{work['account_id']}:{stage}", work["account_id"])
        self.finish_runs()

    def summary(self):
        return {
            "accounts": self.all("SELECT * FROM accounts ORDER BY id"),
            "runs": self.all("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 100"),
            "work_states": self.all(
                "SELECT processing_state,count(*) AS count FROM works GROUP BY processing_state"
            ),
            "attention": self.all(
                "SELECT id,processing_state,last_error_code,external_job_id FROM works WHERE processing_state IN ('blocked','failed','submit_unknown')"
            ),
            "notifications": self.all(
                "SELECT id,kind,state,last_error_code,cancel_reason FROM notifications ORDER BY id DESC LIMIT 100"
            ),
        }
