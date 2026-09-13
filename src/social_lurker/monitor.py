"""Bounded, round-robin publication discovery. No host scheduling or message side effects."""

import time
from collections import deque

from .config import automatic_gate
from .errors import LurkerError, require
from .schedule import allowed, next_slot
from .store import Store, active, qualify
from .util import ident, lock

GATES = {
    "API_HELD",
    "API_RATE_WAIT",
    "TIME_SLICE_END",
    "QUIET_HOURS",
    "MAINTENANCE_ACTIVE",
    "WATCH_CHANGED",
    "SOURCE_UNVERIFIED",
    "HOST_AUTOMATIC_UNVERIFIED",
}


class Monitor:
    def __init__(self, instance, source, *, monotonic=time.monotonic):
        self.instance, self.source, self.monotonic = instance, source, monotonic
        self.store = Store(instance)

    def poll(self, *, automatic=False, watch_id=None):
        settings = self.instance.load()
        now = int(self.instance.clock())
        if automatic and not allowed(now, settings):
            return {"quiet": True, "reason": "quiet_hours", "pages": 0}
        deadline = self.monotonic() + settings["limits"]["poll_seconds"]
        pages = 0
        errors = []
        scans = {}
        with lock(self.instance.root, "poll.lock", timeout=0):
            with self.instance.transaction("permit") as (db, settings):
                if automatic and not allowed(now, settings):
                    return {"quiet": True, "pages": 0, "reason": "quiet_hours"}
                if automatic:
                    automatic_gate(settings)
                claimed, recovered = self.store.recover_and_claim(db, settings, now, automatic)
                if not claimed:
                    return {"quiet": True, "pages": 0, "reason": "slot_already_claimed"}
                if watch_id:
                    active(db, watch_id)
                rows = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM watches WHERE status='active' ORDER BY next_check_at,id"
                    )
                    if (watch_id is None or r["id"] == watch_id)
                    and (not automatic or r["next_check_at"] <= now)
                ]
            queue = deque((r["id"], r["generation"], False) for r in rows)
            while queue and self.monotonic() + 35 <= deadline:
                uid, generation, started = queue.popleft()
                try:
                    with self.instance.transaction("permit") as (db, settings):
                        w = active(db, uid, generation)
                        if automatic:
                            require(
                                self.source.capability(settings, w["platform"], w["source_variant"])
                                != "unverified",
                                "SOURCE_UNVERIFIED",
                                "平台适配尚待真实验证",
                            )
                        # Persistent provider holds are checked before marking an author attempt.
                        from .state import applicable, holds

                        endpoint = self.source.list_endpoint(w)
                        require(not any(applicable(h, endpoint) for h in holds(db)), "API_HELD")
                        rt = db.execute("SELECT api_blocked_until FROM runtime").fetchone()[0]
                        require(rt is None or rt <= self.instance.clock(), "API_RATE_WAIT")
                        scan = self.store.begin_scan(db, w, settings, int(self.instance.clock()))
                        if scan["page_count"] > 0:
                            require(scan["continuation"] == "all_new", "SCAN_INVALID")
                        if not started:
                            db.execute(
                                "UPDATE watches SET last_attempt_at=?,next_check_at=?,updated_at=? WHERE id=?",
                                (
                                    int(self.instance.clock()),
                                    next_slot(self.instance.clock(), settings),
                                    int(self.instance.clock()),
                                    uid,
                                ),
                            )
                    page = self.source.list_publications(
                        w, scan["cursor"], automatic=automatic, deadline=deadline
                    )
                    more = self.store.page(uid, generation, scan, page)
                    pages += 1
                    progress = scans.setdefault(uid, {"watch_id": uid, "pages": 0, "stop_reason": None})
                    progress["pages"] += 1
                    if page.excluded_count:
                        progress["excluded_collaborations"] = (
                            progress.get("excluded_collaborations", 0) + page.excluded_count
                        )
                    progress["stop_reason"] = (
                        "continuation_pending"
                        if more
                        else (
                            "initial_one_page"
                            if scan["initial_check"]
                            else ("endpoint_end" if page.end_state == "confirmed_end" else "not_all_new")
                        )
                    )
                    if more:
                        queue.append((uid, generation, True))
                except LurkerError as error:
                    if error.code not in GATES or getattr(error, "request_attempted", False):
                        self.store.failure(uid, generation, error)
                    errors.append({"watch_id": uid, "code": error.code})
                    scans.setdefault(uid, {"watch_id": uid, "pages": 0})["stop_reason"] = error.code
                    if error.code in {"TIME_SLICE_END", "QUIET_HOURS", "MAINTENANCE_ACTIVE", "API_RATE_WAIT"}:
                        break
            # Metadata has its own already-frozen eligibility and does not update watch attempts.
            with self.instance.transaction("read") as (db, _):
                pending = [
                    r[0]
                    for r in db.execute(
                        "SELECT u.id FROM updates u JOIN watches w ON w.id=u.watch_id WHERE u.state='blocked' AND u.next_attempt_at IS NOT NULL AND u.next_attempt_at<=? AND w.status='active' AND u.eligible_generation=w.generation ORDER BY u.next_attempt_at,u.id",
                        (int(self.instance.clock()),),
                    )
                ]
            for uid in pending:
                if self.monotonic() + 35 > deadline:
                    break
                try:
                    self.metadata(uid, automatic=automatic, deadline=deadline)
                except LurkerError as error:
                    errors.append({"update_id": uid, "code": error.code})
                    if error.code in {"TIME_SLICE_END", "QUIET_HOURS", "MAINTENANCE_ACTIVE", "API_RATE_WAIT"}:
                        break
        return {
            "quiet": pages == 0 and not errors,
            "pages": pages,
            "recovered_watches": recovered,
            "scans": list(scans.values()),
            "errors": errors,
        }

    def metadata(self, update_id, *, automatic=False, deadline=None):
        with self.instance.transaction("permit") as (db, settings):
            record = db.execute("SELECT * FROM updates WHERE id=?", (update_id,)).fetchone()
            require(record is not None and record["state"] == "blocked", "UPDATE_CHANGED")
            row = dict(record)
            w = active(db, row["watch_id"], row["eligible_generation"])
            if automatic:
                require(
                    self.source.capability(settings, w["platform"], w["source_variant"]) != "unverified",
                    "SOURCE_UNVERIFIED",
                )
        try:
            p = self.source.complete_metadata(w, row, automatic=automatic, deadline=deadline)
        except LurkerError as error:
            if error.code not in GATES or getattr(error, "request_attempted", False):
                with self.instance.transaction("result") as (db, settings):
                    active(db, w["id"], w["generation"])
                    latest = db.execute(
                        "SELECT * FROM updates WHERE id=? AND state='blocked'", (update_id,)
                    ).fetchone()
                    if latest:
                        wait = next_slot(self.instance.clock(), settings) if error.retryable else None
                        db.execute(
                            "UPDATE updates SET error_code=?,failure_count=failure_count+1,next_attempt_at=? WHERE id=?",
                            (error.code, wait, update_id),
                        )
            raise
        with self.instance.transaction("result") as (db, _):
            active(db, w["id"], w["generation"])
            latest = db.execute("SELECT * FROM updates WHERE id=?", (update_id,)).fetchone()
            require(latest is not None and latest["state"] == "blocked", "UPDATE_CHANGED")
            require(
                p.author_id == w["author_id"]
                and p.work_id == row["work_id"]
                and p.platform == row["platform"],
                "PAGE_IDENTITY_INVALID",
            )
            state = qualify(db, dict(latest), p, int(self.instance.clock()), exhausted=True)
        return {"update_id": update_id, "state": state}

    def test_latest(self, watch_id):
        with lock(self.instance.root, "poll.lock", timeout=0):
            with self.instance.transaction("permit") as (db, _):
                w = active(db, watch_id)
            page = self.source.list_publications(w)
            require(
                all(p.platform == w["platform"] and p.author_id == w["author_id"] for p in page.items),
                "PAGE_IDENTITY_INVALID",
            )
            now = int(self.instance.clock())
            candidates = [
                p
                for p in page.items
                if p.author_id == w["author_id"] and p.published_at is not None and p.published_at <= now
            ]
            require(candidates, "NO_AVAILABLE_PUBLICATION", "接口本次没有可试发的有效作品")
            p = max(candidates, key=lambda p: (p.published_at, p.work_id))
            with self.instance.transaction("result") as (db, _):
                active(db, watch_id, w["generation"])
                row = db.execute(
                    "SELECT * FROM updates WHERE platform=? AND work_id=?", (p.platform, p.work_id)
                ).fetchone()
                if row:
                    require(row["watch_id"] == watch_id, "PAGE_IDENTITY_INVALID")
                    if (
                        row["state"] == "ignored"
                        and row["error_code"] == "BEFORE_WINDOW"
                        and all(
                            row[k] is None
                            for k in ("attempt_id", "payload", "provider_message_id", "sent_at")
                        )
                    ):
                        db.execute(
                            "UPDATE updates SET state='blocked',reason='test',eligible_generation=?,eligibility_lower=0,eligibility_upper=?,next_attempt_at=? WHERE id=?",
                            (w["generation"], now, now, row["id"]),
                        )
                        row = db.execute("SELECT * FROM updates WHERE id=?", (row["id"],)).fetchone()
                        qualify(db, dict(row), p, now)
                    return dict(db.execute("SELECT * FROM updates WHERE id=?", (row["id"],)).fetchone())
                from .store import source_url

                link = source_url(p.platform, p.source_url)
                uid = ident()
                db.execute(
                    "INSERT INTO updates(id,watch_id,platform,work_id,author_name,published_at,title,source_url,cover_url,first_seen_at,last_seen_at,eligible_generation,eligibility_lower,eligibility_upper,reason,state,next_attempt_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,'test',?,?)",
                    (
                        uid,
                        watch_id,
                        p.platform,
                        p.work_id,
                        p.author_name,
                        p.published_at,
                        p.title[:4096],
                        link,
                        p.cover_url,
                        now,
                        now,
                        w["generation"],
                        now,
                        "queued" if link else "blocked",
                        None if link else now,
                    ),
                )
                return dict(db.execute("SELECT * FROM updates WHERE id=?", (uid,)).fetchone())
