import json
import shutil
import time

from .errors import LurkerError, require
from .providers.fal import MODEL, Fal
from .providers.tikhub import TikHub
from .store import ACTIVE_RUNS, OPEN_RUNS
from .util import digest, file_lock, json_text, now, safe_path, token


class Engine:
    def __init__(self, instance, store, settings, social=None, media=None, asr=None):
        self.instance, self.store, self.settings = instance, store, settings
        self._social, self._media, self._asr = social, media, asr
        self.pages = 0
        self.new_works = 0
        self.deadline = time.monotonic() + settings["execution"]["tick_soft_seconds"]

    @property
    def social(self):
        if self._social is None:
            self._social = TikHub(self.instance.credentials().get("TIKHUB_API_KEY"))
        return self._social

    @property
    def asr(self):
        if self._asr is None:
            self._asr = Fal(self.instance.credentials().get("FAL_KEY"))
        return self._asr

    @property
    def media(self):
        if self._media is None:
            from .media import Media

            self._media = Media(self.settings)
        return self._media

    def budget(self):
        return time.monotonic() < self.deadline

    def schedule(self):
        with self.store.tx():
            for account in self.store.all(
                "SELECT * FROM accounts WHERE tracking_state='active' AND (next_check_at IS NULL OR next_check_at<=?)",
                (now(),),
            ):
                existing = self.store.one(
                    f"""SELECT id FROM collection_runs WHERE account_id=? AND watch_epoch=?
                  AND kind IN ('initial','poll') AND state IN {OPEN_RUNS} AND enumeration_complete=0""",
                    (account["id"], account["watch_epoch"]),
                )
                if existing:
                    continue
                start = max(
                    account["watch_started_at"],
                    (account["coverage_until"] or account["watch_started_at"])
                    - self.settings["execution"]["lookback_hours"] * 3600,
                )
                self.store.create_run(account, "poll", start=start, end=now())
                self.store.execute(
                    "UPDATE accounts SET next_check_at=? WHERE id=?",
                    (now() + self.settings["check_interval_minutes"] * 60, account["id"]),
                )

    def enumerate_run(self, run_id):
        run = self.store.run(run_id)
        account = self.store.account(run["account_id"])
        with self.store.tx():
            run = self.store.run(run_id)
            account = self.store.account(run["account_id"])
            if run["state"] == "queued":
                if account["tracking_state"] != "active" or account["watch_epoch"] != run["watch_epoch"]:
                    self.store.execute(
                        "UPDATE collection_runs SET state='canceled',finished_at=? WHERE id=?",
                        (now(), run_id),
                    )
                    return
                self.store.execute(
                    "UPDATE collection_runs SET state='running',started_at=? WHERE id=?", (now(), run_id)
                )
            elif run["state"] not in ("running", "retry_wait"):
                return
            self.store.execute(
                "UPDATE accounts SET last_check_started_at=? WHERE id=?", (now(), account["id"])
            )
        cursor = json.loads(run["cursor"]) if run["cursor"] else {"value": None, "seen": []}
        while self.budget() and self.pages < self.settings["execution"]["max_pages_per_tick"]:
            try:
                page = self.social.page(account, cursor["value"])
                self.pages += 1
                finished = page.has_more is False
                repeated = (
                    not page.cursor or digest(page.cursor) in cursor["seen"] or page.cursor == cursor["value"]
                )
                with self.store.tx():
                    for item in page.items:
                        if item.published_at is None or item.published_at <= run["range_end"]:
                            if (
                                run["scope_type"] != "time"
                                or item.published_at is None
                                or item.published_at >= (run["range_start"] or 0)
                            ):
                                self.store.discover(run, item)
                    if cursor["value"]:
                        cursor["seen"].append(digest(cursor["value"]))
                    cursor["value"] = page.cursor
                    self.store.execute(
                        "UPDATE collection_runs SET cursor=?,last_page_at=?,state='running',last_error_code=NULL WHERE id=?",
                        (json_text(cursor), now(), run_id),
                    )
                if finished:
                    self.select_scope(run_id)
                    return
                if repeated:
                    raise LurkerError(
                        "ENUMERATION_UNCERTAIN", "没有可靠尾页证据或游标循环；保留元信息，未声称取全"
                    )
            except LurkerError as error:
                if error.code == "BUSY":
                    return
                if error.code == "RESPONSE_INVALID":
                    error.retryable = True
                attempts = self.store.run(run_id)["attempt_count"] + 1
                state = (
                    "retry_wait"
                    if error.retryable and attempts < 3
                    else ("failed" if error.retryable else "blocked")
                )
                with self.store.tx():
                    self.store.execute(
                        "UPDATE collection_runs SET state=?,attempt_count=?,last_error_code=?,next_attempt_at=? WHERE id=?",
                        (
                            state,
                            attempts,
                            error.code,
                            now() + max(60 if attempts == 1 else 300, error.retry_after or 0),
                            run_id,
                        ),
                    )
                    self.store.execute(
                        "UPDATE accounts SET last_error_code=? WHERE id=?", (error.code, account["id"])
                    )
                    if state in ("blocked", "failed"):
                        self.store.incident(error.code, f"account:{account['id']}:enumeration", account["id"])
                return

    def select_scope(self, run_id):
        with self.store.tx():
            run = self.store.run(run_id)
            unknown = self.store.one(
                """SELECT w.id FROM works w JOIN collection_items i ON i.work_id=w.id
              WHERE i.run_id=? AND w.published_at IS NULL LIMIT 1""",
                (run_id,),
            )
            if unknown and run["scope_type"] != "all":
                self.store.execute(
                    "UPDATE collection_runs SET state='blocked',last_error_code='PUBLISHED_AT_UNKNOWN' WHERE id=?",
                    (run_id,),
                )
                self.store.incident(
                    "PUBLISHED_AT_UNKNOWN", f"account:{run['account_id']}:enumeration", run["account_id"]
                )
                return
            if run["scope_type"] in ("latest", "count"):
                limit = 1 if run["scope_type"] == "latest" else run["requested_count"]
                self.store.execute(
                    """DELETE FROM collection_items WHERE run_id=? AND work_id NOT IN
                  (SELECT w.id FROM works w JOIN collection_items i ON i.work_id=w.id WHERE i.run_id=?
                   ORDER BY w.published_at DESC,w.platform_work_id DESC LIMIT ?)""",
                    (run_id, run_id, limit),
                )
            account = self.store.account(run["account_id"])
            self.store.execute(
                """UPDATE collection_runs SET enumeration_complete=1,state='running',reported_total=
              (SELECT count(*) FROM collection_items WHERE run_id=?),total_basis='accessible_metadata_enumeration',
              total_observed_at=?,cursor=NULL WHERE id=?""",
                (run_id, now(), run_id),
            )
            if run["kind"] != "history":
                self.store.execute(
                    "UPDATE collection_items SET requires_notification=1 WHERE run_id=?", (run_id,)
                )
                for item in self.store.all("SELECT work_id FROM collection_items WHERE run_id=?", (run_id,)):
                    self.store.notify_work(item["work_id"])
                if account["watch_epoch"] == run["watch_epoch"] and account["tracking_state"] == "active":
                    self.store.execute(
                        """UPDATE accounts SET coverage_until=?,last_check_completed_at=?,
                      next_check_at=?,last_error_code=NULL WHERE id=?""",
                        (
                            run["range_end"],
                            now(),
                            now() + self.settings["check_interval_minutes"] * 60,
                            account["id"],
                        ),
                    )
            self.store.resolve_incident("ENUMERATION_UNCERTAIN", f"account:{account['id']}:enumeration")
        self.store.finish_runs()

    def recover(self):
        # Called only under OS instance lock. A live process holding that lock cannot be stolen by TTL.
        with self.store.tx():
            self.store.execute("""UPDATE works SET processing_state='submit_unknown',last_error_code='ASR_SUBMIT_UNKNOWN',
              owner_token=NULL,lease_until=NULL WHERE asr_phase='submitting' AND external_job_id IS NULL
              AND processing_state NOT IN ('ready','no_speech')""")
            self.store.execute(f"""UPDATE collection_items SET result='blocked',error_code='ASR_SUBMIT_UNKNOWN'
              WHERE result='pending' AND work_id IN (SELECT id FROM works WHERE processing_state='submit_unknown')
              AND run_id IN (SELECT id FROM collection_runs WHERE state IN {OPEN_RUNS})""")
            self.store.execute(
                """UPDATE notifications SET state='unknown',last_error_code='DELIVERY_UNKNOWN',updated_at=?
              WHERE state='sending' AND updated_at<?""",
                (now(), now() - 600),
            )
            for work in self.store.all(
                "SELECT id,account_id FROM works WHERE processing_state='submit_unknown'"
            ):
                self.store.incident(
                    "ASR_SUBMIT_UNKNOWN", f"account:{work['account_id']}:asr", work["account_id"]
                )

    def choose_work(self):
        # A known remote job and proofreading handoff occupy the single active slot across wakes.
        active = self.store.one("""SELECT * FROM works WHERE processing_state IN
          ('fetching','transcribing','pending_proofread','proofreading') OR
          (processing_state='retry_wait' AND external_job_id IS NOT NULL) ORDER BY id LIMIT 1""")
        if active:
            return active
        return self.store.one(
            f"""SELECT w.* FROM works w JOIN collection_items i ON i.work_id=w.id
          JOIN collection_runs r ON r.id=i.run_id WHERE r.state IN {ACTIVE_RUNS} AND r.enumeration_complete=1
          AND r.plan_confirmed=1 AND i.result='pending' AND w.processing_state IN ('pending','retry_wait')
          AND (w.next_attempt_at IS NULL OR w.next_attempt_at<=?)
          ORDER BY CASE r.kind WHEN 'initial' THEN 0 WHEN 'poll' THEN 1 ELSE 2 END,w.published_at DESC,w.id LIMIT 1""",
            (now(),),
        )

    def heartbeat(self, work_id, owner):
        last = [0]

        def beat():
            if now() - last[0] >= self.settings["execution"]["lease_renew_seconds"]:
                changed = self.store.execute(
                    "UPDATE works SET lease_until=? WHERE id=? AND owner_token=?",
                    (now() + self.settings["execution"]["lease_seconds"], work_id, owner),
                ).rowcount
                require(changed == 1, "STALE_OWNER", "任务执行权已变更")
                last[0] = now()

        return beat

    def advance_work(self, work):
        if work["processing_state"] in ("pending_proofread", "proofreading"):
            return False
        if work["next_attempt_at"] and work["next_attempt_at"] > now():
            return False
        if (
            not work["external_job_id"]
            and self.new_works >= self.settings["execution"]["max_new_works_per_tick"]
        ):
            return False
        owner = token()
        with self.store.tx():
            self.store.execute(
                "UPDATE works SET owner_token=?,lease_until=?,updated_at=? WHERE id=?",
                (owner, now() + self.settings["execution"]["lease_seconds"], now(), work["id"]),
            )
        heartbeat = self.heartbeat(work["id"], owner)
        stage = "query" if work["external_job_id"] else "media"
        try:
            if work["external_job_id"]:
                require(
                    work["asr_provider"] == "fal" and work["asr_model"] == MODEL,
                    "ASR_BINDING_MISMATCH",
                    "远端任务服务来源不匹配",
                )
                if (
                    now() - max(work["asr_submitted_at"] or work["created_at"], work["asr_reviewed_at"] or 0)
                    > 86400
                ):
                    raise LurkerError("ASR_STALE", "远端任务已超过 24 小时，保留任务 ID 待核对")
                state = self.asr.query(work["external_job_id"])
                if state != "COMPLETED":
                    elapsed = now() - (work["asr_submitted_at"] or now())
                    self.store.execute(
                        "UPDATE works SET processing_state='transcribing',next_attempt_at=?,owner_token=NULL,lease_until=NULL WHERE id=?",
                        (now() + min(30, 10 + elapsed / 10), work["id"]),
                    )
                    return False
                stage = "result"
                raw = self.asr.fetch_result(work["external_job_id"])
                require(
                    work["media_duration_ms"] and work["media_duration_ms"] > 0,
                    "MEDIA_INVALID",
                    "缺少已验证音轨证据",
                )
                self.store.finish_work(
                    work["id"],
                    "pending_proofread" if raw.strip() else "no_speech",
                    owner=owner,
                    raw=raw,
                    source="fal-ai/whisper",
                )
                try:
                    self.cleanup_work(self.store.work(work["id"]))
                except OSError:
                    pass
                return True
            self.new_works += 1
            account = self.store.account(work["account_id"])
            self.store.execute(
                "UPDATE works SET processing_state='fetching',updated_at=? WHERE id=?", (now(), work["id"])
            )
            # Obtain credentials before downloading anything. This doesn't make a paid call.
            asr = self.asr
            with file_lock(self.instance.root / "runtime/locks/heavy.lock"):
                detail = self.social.detail(account, work)
                source = self.social.media_source(account["platform"], detail)
                if detail.source_url:
                    self.store.execute(
                        "UPDATE works SET source_url=? WHERE id=?", (detail.source_url, work["id"])
                    )
                relative = (
                    work["work_relpath"]
                    or f"work/{work['id']}/{work['execution_cycle']}-{work['attempt_count'] + 1}"
                )
                directory = safe_path(self.instance.path, relative)
                self.store.execute("UPDATE works SET work_relpath=? WHERE id=?", (relative, work["id"]))
                audio = self.media.prepare(source, directory, heartbeat)
            self.store.execute(
                "UPDATE works SET media_duration_ms=?,asr_phase=? WHERE id=?",
                (audio.duration_ms, "prepared", work["id"]),
            )
            stage = "upload"
            url = asr.upload(audio.path, heartbeat)
            stage = "submit"
            operation = token()
            with self.store.tx():
                self.store.execute(
                    """UPDATE works SET asr_phase='submitting',operation_token=?,asr_provider='fal',
                  asr_model=?,processing_state='transcribing',asr_submitted_at=? WHERE id=? AND owner_token=?""",
                    (operation, MODEL, now(), work["id"], owner),
                )
            job_id = asr.submit(url)
            # The durable pre-intent makes a crash here unknown, never another paid submission.
            with self.store.tx():
                self.store.execute(
                    """UPDATE works SET external_job_id=?,asr_phase='submitted',next_attempt_at=?,
                  owner_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND owner_token=?""",
                    (job_id, now() + 10, now(), work["id"], owner),
                )
            return False
        except LurkerError as error:
            if error.code == "BUSY":
                self.store.execute(
                    "UPDATE works SET processing_state='pending',owner_token=NULL,lease_until=NULL WHERE id=?",
                    (work["id"],),
                )
                return False
            if error.code == "ASR_SUBMIT_UNKNOWN":
                with self.store.tx():
                    self.store.execute(
                        "UPDATE works SET processing_state='submit_unknown',last_error_code=?,owner_token=NULL,lease_until=NULL WHERE id=?",
                        (error.code, work["id"]),
                    )
                    self.store.execute(
                        "UPDATE collection_items SET result='blocked',error_code=? WHERE work_id=? AND result='pending'",
                        (error.code, work["id"]),
                    )
                    self.store.incident(error.code, f"account:{work['account_id']}:asr", work["account_id"])
                self.store.finish_runs()
                return True
            if stage == "submit":
                # A known rejection, not an uncertain submit. Safe to retry within the stage budget.
                self.store.execute("UPDATE works SET asr_phase='rejected' WHERE id=?", (work["id"],))
            if work["external_job_id"] and error.code == "UNAVAILABLE":
                error = LurkerError("ASR_TASK_UNAVAILABLE", "旧任务无法查询，保留原任务 ID 并核对账号")
            if error.code == "UNSUPPORTED_MEDIA":
                error = LurkerError("UNAVAILABLE", "作品类型或音轨不受支持")
            self.store.fail_work(work["id"], stage, error, self.settings)
            return True

    def cleanup_work(self, work):
        if not work["work_relpath"]:
            return
        directory = safe_path(self.instance.path, work["work_relpath"])
        if directory.exists():
            shutil.rmtree(directory)
        self.store.execute("UPDATE works SET work_relpath=NULL WHERE id=?", (work["id"],))

    def cleanup(self):
        for work in self.store.all("SELECT * FROM works WHERE work_relpath IS NOT NULL"):
            successful = work["raw_transcript_text"] is not None
            expired = (
                work["processing_state"] in ("failed", "unavailable")
                and now() - work["updated_at"] > self.settings["retention"]["failed_work_days"] * 86400
            )
            if successful or expired:
                try:
                    self.cleanup_work(work)
                except OSError:
                    pass  # Retain registered path; retry cleanup on another wake.
        for account in self.store.all("SELECT * FROM accounts WHERE tracking_state='deleting'"):
            inflight = self.store.one(
                f"SELECT id FROM collection_runs WHERE account_id=? AND state IN {ACTIVE_RUNS}",
                (account["id"],),
            )
            if inflight or self.store.one(
                "SELECT id FROM works WHERE account_id=? AND processing_state IN ('fetching','transcribing','pending_proofread','proofreading') LIMIT 1",
                (account["id"],),
            ):
                continue
            try:
                for work in self.store.all("SELECT * FROM works WHERE account_id=?", (account["id"],)):
                    self.cleanup_work(work)
            except OSError:
                self.store.execute(
                    "UPDATE accounts SET last_error_code='CLEANUP_FAILED' WHERE id=?", (account["id"],)
                )
                continue
            with self.store.tx():
                self.store.execute("DELETE FROM accounts WHERE id=?", (account["id"],))

    def recover_fixed_configuration(self):
        from .config import doctor
        from .operations import reset_work

        paths = [self.instance.settings_path, self.instance.path / ".env"]
        modified = max((p.stat().st_mtime for p in paths if p.exists()), default=0)
        diagnostics = None
        for work in self.store.all("SELECT * FROM works WHERE processing_state='blocked'"):
            code = work["last_error_code"]
            changed = (
                code in {"CREDENTIALS_MISSING", "AUTH_REQUIRED", "QUOTA_EXHAUSTED", "MEDIA_LIMIT"}
                and modified > work["updated_at"]
            )
            environmental = code in {"DEPENDENCY_MISSING", "DISK_SPACE"}
            if changed or environmental:
                if diagnostics is None:
                    diagnostics = doctor(self.instance, self.settings)
                if not diagnostics["local_ready"]:
                    continue
                if code == "DISK_SPACE":
                    needed = (
                        self.settings["execution"]["min_free_bytes"]
                        + self.settings["media"]["max_source_bytes"] * 2
                        + self.settings["media"]["max_audio_bytes"]
                    )
                    if shutil.disk_usage(self.instance.path).free < needed:
                        continue
                with self.store.tx():
                    reset_work(self.store, work, {})

    def tick(self, *, schedule=True):
        self.recover()
        self.recover_fixed_configuration()
        maintenance = (self.instance.maintenance / "upgrade.json").exists()
        if schedule and not maintenance:
            self.schedule()
        runs = self.store.all(
            """SELECT * FROM collection_runs WHERE state IN ('queued','running','retry_wait')
          AND enumeration_complete=0 AND (next_attempt_at IS NULL OR next_attempt_at<=?)
          ORDER BY CASE kind WHEN 'initial' THEN 0 WHEN 'poll' THEN 1 ELSE 2 END,id""",
            (now(),),
        )
        for run in runs:
            if not self.budget():
                break
            if maintenance and run["state"] == "queued":
                continue
            self.enumerate_run(run["id"])
        while self.budget():
            work = self.choose_work()
            if not work:
                break
            if not self.advance_work(work):
                current = self.store.work(work["id"])
                due = current["next_attempt_at"]
                if current["processing_state"] == "transcribing" and due is not None:
                    delay = max(0, due - now())
                    if time.monotonic() + delay + 1 < self.deadline:
                        time.sleep(min(30, delay))
                        continue
                break
        self.store.finish_runs()
        self.cleanup()
        return {
            "enumerated_pages": self.pages,
            "started_works": self.new_works,
            "maintenance": maintenance,
            "grok_acceptance_pending": True,
        }
