"""Watch lifecycle, page-atomic discovery and frozen candidate eligibility."""

from .errors import LurkerError, require
from .schedule import context, next_slot
from .state import add_gap, incident, resolve_watch_incidents
from .util import canonical, clean_text, digest, ident, parse_json, platform_id, public_cover_url, public_url

PLATFORMS = {"douyin", "wechat_channels"}
FACTS = ("attempt_id", "send_started_at", "payload", "payload_hash", "provider_message_id", "sent_at")


def watched(db, watch_id):
    row = db.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    require(row is not None, "WATCH_NOT_FOUND")
    return dict(row)


def active(db, watch_id, generation=None):
    row = watched(db, watch_id)
    require(
        row["status"] == "active" and (generation is None or row["generation"] == generation), "WATCH_CHANGED"
    )
    return row


def update_row(db, uid, values):
    columns = {r[1] for r in db.execute("PRAGMA table_info(updates)")}
    require(set(values) <= columns, "STATE_INVALID")
    db.execute(
        "UPDATE updates SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", (*values.values(), uid)
    )


def source_url(platform, value):
    return public_url(value, ("douyin.com", "iesdouyin.com") if platform == "douyin" else ("weixin.qq.com",))


def qualify(db, row, publication, now, *, exhausted=False):
    """Only unpermitted live candidates may receive metadata; eligibility never drifts."""
    require(row["state"] in {"blocked", "queued"}, "UPDATE_CHANGED")
    values = {}
    if row["payload"] is None:
        values = {
            "author_name": clean_text(publication.author_name or row["author_name"], 512),
            "title": clean_text(publication.title, 4096),
            "last_seen_at": now,
        }
        if publication.cover_url:
            values["cover_url"] = public_cover_url(publication.cover_url)
    if publication.published_at is not None:
        values["published_at"] = publication.published_at
    if publication.source_url:
        values["source_url"] = source_url(row["platform"], publication.source_url)
    candidate = {**row, **values}
    published = candidate["published_at"]
    if published is not None and published > candidate["eligibility_upper"]:
        require(row["state"] == "blocked" and all(row[k] is None for k in FACTS), "FUTURE_SEND_CONFLICT")
        db.execute("DELETE FROM updates WHERE id=?", (row["id"],))
        return "future"
    if published is not None and published < candidate["eligibility_lower"] and candidate["reason"] != "test":
        values.update(state="ignored", error_code="BEFORE_WINDOW", next_attempt_at=None)
    elif published is not None and candidate["source_url"]:
        values.update(state="queued", error_code=None, next_attempt_at=None)
    else:
        values.update(
            state="blocked",
            error_code="METADATA_INCOMPLETE",
            next_attempt_at=None if exhausted else row["next_attempt_at"],
        )
    if len(publication.title) > 4096 and values.get("state") == "queued":
        values["error_code"] = "TITLE_TRUNCATED"
    update_row(db, row["id"], values)
    return values["state"]


class Store:
    def __init__(self, instance):
        self.instance = instance

    def list(self):
        with self.instance.transaction("read") as (db, _):
            rows = []
            for raw in db.execute("SELECT * FROM watches ORDER BY platform,author_name,id"):
                row = dict(raw)
                row["scan_pending"] = row.pop("scan_state_json") is not None
                rows.append(row)
            return rows

    def add(self, author):
        require(author.platform in PLATFORMS, "PLATFORM_UNSUPPORTED")
        aid = platform_id(author.author_id)
        now = int(self.instance.clock())
        with self.instance.transaction() as (db, settings):
            old = db.execute(
                "SELECT * FROM watches WHERE platform=? AND author_id=?", (author.platform, aid)
            ).fetchone()
            if old and old["status"] != "removed":
                return dict(old)
            zero = db.execute("SELECT count(*) FROM watches WHERE status='active'").fetchone()[0] == 0
            if old:
                uid = old["id"]
                db.execute(
                    "UPDATE watches SET status='active',generation=generation+1,scan_state_json=NULL,watch_since=?,discovery_floor=?,initial_check_pending=1,next_check_at=?,updated_at=?,error_code=NULL,failure_count=0 WHERE id=?",
                    (now, now, next_slot(now, settings), now, uid),
                )
            else:
                uid = ident()
                db.execute(
                    "INSERT INTO watches(id,platform,author_id,author_name,profile_url,source_variant,status,watch_since,discovery_floor,next_check_at,created_at,updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?,?,?)",
                    (
                        uid,
                        author.platform,
                        aid,
                        clean_text(author.author_name, 512),
                        author.profile_url,
                        author.variant,
                        now,
                        now,
                        next_slot(now, settings),
                        now,
                        now,
                    ),
                )
            if zero:
                db.execute("UPDATE runtime SET recovery_applied_slot=?", (context(now, settings)[0],))
            return watched(db, uid)

    def transition(self, watch_id, target):
        require(target in {"active", "paused", "removed"}, "STATUS_INVALID")
        now = int(self.instance.clock())
        with self.instance.transaction() as (db, settings):
            row = watched(db, watch_id)
            if row["status"] == target:
                return row
            require(not (row["status"] == "removed" and target == "active"), "READD_REQUIRED")
            zero = db.execute("SELECT count(*) FROM watches WHERE status='active'").fetchone()[0] == 0
            db.execute(
                "UPDATE watches SET status=?,generation=generation+1,scan_state_json=NULL,next_check_at=?,updated_at=? WHERE id=?",
                (target, next_slot(now, settings), now, watch_id),
            )
            if target == "active":
                db.execute(
                    "UPDATE watches SET watch_since=?,discovery_floor=?,error_code=NULL,failure_count=0 WHERE id=?",
                    (now, now, watch_id),
                )
                if zero:
                    db.execute("UPDATE runtime SET recovery_applied_slot=?", (context(now, settings)[0],))
            else:
                db.execute(
                    "UPDATE updates SET state='cancelled',next_attempt_at=NULL WHERE watch_id=? AND state IN ('blocked','queued')",
                    (watch_id,),
                )
            return watched(db, watch_id)

    def purge(self, watch_id, confirmed=False):
        require(confirmed is True, "PURGE_CONFIRMATION_REQUIRED")
        with self.instance.transaction() as (db, _):
            row = watched(db, watch_id)
            require(row["status"] != "active", "PAUSE_REQUIRED")
            require(
                not db.execute(
                    "SELECT 1 FROM updates WHERE watch_id=? AND state IN ('sending','unknown')", (watch_id,)
                ).fetchone(),
                "UNRESOLVED_DELIVERY",
            )
            db.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        return {"purged": watch_id}

    def recover_and_claim(self, db, settings, now, automatic):
        slot, previous = context(now, settings)
        runtime = dict(db.execute("SELECT * FROM runtime").fetchone())
        anchors = [
            v for v in (runtime["last_automatic_slot"], runtime["recovery_applied_slot"]) if v is not None
        ]
        changed = []
        if anchors:
            baseline = max(anchors)
            for record in db.execute("SELECT * FROM watches WHERE status='active'").fetchall():
                w = dict(record)
                attempt = (
                    w["last_attempt_at"]
                    if w["last_attempt_at"] is not None and w["last_attempt_at"] >= w["watch_since"]
                    else w["watch_since"]
                )
                if previous > max(baseline, w["watch_since"], attempt):
                    gaps = add_gap(
                        w["coverage_gaps_json"],
                        max(baseline, w["discovery_floor"], attempt),
                        now,
                        "offline_no_catchup",
                    )
                    db.execute(
                        "UPDATE watches SET discovery_floor=max(discovery_floor,?),scan_state_json=NULL,coverage_state='gapped',coverage_gaps_json=?,updated_at=? WHERE id=?",
                        (now, gaps, now, w["id"]),
                    )
                    changed.append(w["id"])
            if changed:
                db.execute("UPDATE runtime SET recovery_applied_slot=?", (slot,))
        if automatic:
            if runtime["last_automatic_slot"] is not None and runtime["last_automatic_slot"] >= slot:
                return False, changed
            db.execute("UPDATE runtime SET last_automatic_slot=?,updated_at=?", (slot, now))
        return True, changed

    def begin_scan(self, db, w, settings, now):
        scan = parse_json(w["scan_state_json"]) if w["scan_state_json"] else None
        if scan and scan["generation"] != w["generation"]:
            scan = None
        if scan and now - scan["started_at"] > settings["monitor"]["max_scan_age_seconds"]:
            gap = add_gap(w["coverage_gaps_json"], scan["lower"], scan["upper"], "cursor_expired")
            db.execute(
                "UPDATE watches SET coverage_gaps_json=?,coverage_state='degraded' WHERE id=?", (gap, w["id"])
            )
            scan = None
        if scan is None:
            base = w["last_scan_upper"] if w["last_scan_upper"] is not None else w["discovery_floor"]
            scan = dict(
                generation=w["generation"],
                lower=max(
                    w["watch_since"], w["discovery_floor"], base - settings["monitor"]["overlap_seconds"]
                ),
                upper=now,
                started_at=now,
                cursor=None,
                page_count=0,
                cursor_hashes=[],
                mode="all_new_pages",
                initial_check=bool(w["initial_check_pending"]),
                continuation=None,
            )
            db.execute("UPDATE watches SET scan_state_json=? WHERE id=?", (canonical(scan), w["id"]))
        return scan

    def page(self, watch_id, generation, scan, page):
        now = int(self.instance.clock())
        with self.instance.transaction("result") as (db, _):
            w = active(db, watch_id, generation)
            require(w["scan_state_json"] and parse_json(w["scan_state_json"]) == scan, "SCAN_CHANGED")
            require(page.end_state in {"more", "confirmed_end"}, "PAGE_PROTOCOL_INVALID")
            require(type(page.excluded_count) is int and page.excluded_count >= 0, "PAGE_PROTOCOL_INVALID")
            require(
                (
                    page.end_state == "more"
                    and page.next_cursor is not None
                    and (bool(page.items) or page.excluded_count > 0)
                )
                or (page.end_state == "confirmed_end" and page.next_cursor is None),
                "PAGE_PROTOCOL_INVALID",
            )
            if page.next_cursor is not None:
                require(
                    isinstance(page.next_cursor, str) and len(page.next_cursor.encode()) <= 16384,
                    "CURSOR_INVALID",
                )
                require(
                    digest(page.next_cursor) not in scan["cursor_hashes"]
                    and page.next_cursor != scan["cursor"],
                    "CURSOR_LOOP",
                )
            items = {}
            for item in page.items:
                require(
                    item.platform == w["platform"] and item.author_id == w["author_id"],
                    "PAGE_IDENTITY_INVALID",
                )
                pid = platform_id(item.work_id)
                if pid in items:
                    require(items[pid] == item, "PAGE_IDENTITY_INVALID")
                items[pid] = item
            existing = {}
            for pid in items:
                row = db.execute(
                    "SELECT * FROM updates WHERE platform=? AND work_id=?", (w["platform"], pid)
                ).fetchone()
                if row:
                    require(row["watch_id"] == w["id"], "PAGE_IDENTITY_INVALID")
                    existing[pid] = dict(row)
            all_new = (
                bool(items)
                and not page.excluded_count
                and all(
                    pid not in existing
                    and p.published_at is not None
                    and scan["lower"] <= p.published_at <= scan["upper"]
                    for pid, p in items.items()
                )
            )
            for pid, p in items.items():
                row = existing.get(pid)
                if row:
                    if row["state"] in {"blocked", "queued"} and row["eligible_generation"] == generation:
                        qualify(db, row, p, now)
                    continue
                if p.published_at is not None and p.published_at > scan["upper"]:
                    continue
                before = p.published_at is not None and p.published_at < scan["lower"]
                link = source_url(w["platform"], p.source_url)
                state = (
                    "ignored" if before else ("queued" if p.published_at is not None and link else "blocked")
                )
                code = (
                    "BEFORE_WINDOW"
                    if before
                    else (
                        "METADATA_INCOMPLETE"
                        if state == "blocked"
                        else ("TITLE_TRUNCATED" if len(p.title) > 4096 else None)
                    )
                )
                db.execute(
                    "INSERT INTO updates(id,watch_id,platform,work_id,author_name,published_at,title,source_url,cover_url,first_seen_at,last_seen_at,eligible_generation,eligibility_lower,eligibility_upper,reason,state,next_attempt_at,error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ident(),
                        w["id"],
                        w["platform"],
                        pid,
                        clean_text(p.author_name or w["author_name"], 512),
                        p.published_at,
                        "" if before else clean_text(p.title, 4096),
                        None if before else link,
                        None if before else public_cover_url(p.cover_url),
                        now,
                        now,
                        generation,
                        scan["lower"],
                        scan["upper"],
                        "new",
                        state,
                        now if state == "blocked" else None,
                        code,
                    ),
                )
            if scan["initial_check"]:
                db.execute("UPDATE watches SET initial_check_pending=0 WHERE id=?", (watch_id,))
            continues = all_new and not scan["initial_check"] and page.end_state == "more"
            if continues:
                new_scan = {
                    **scan,
                    "page_count": scan["page_count"] + 1,
                    "cursor": page.next_cursor,
                    "continuation": "all_new",
                    "cursor_hashes": scan["cursor_hashes"] + [digest(page.next_cursor)],
                }
                encoded = canonical(new_scan)
                require(len(encoded.encode()) <= 256 * 1024, "SCAN_STATE_TOO_LARGE")
                db.execute(
                    "UPDATE watches SET scan_state_json=?,coverage_state='partial',failure_count=0,error_code=NULL,updated_at=? WHERE id=?",
                    (encoded, now, watch_id),
                )
            else:
                from .sources import TikHub

                capability = TikHub(None).capability(self.instance.load(), w["platform"], w["source_variant"])
                coverage = (
                    "ok"
                    if page.end_state == "confirmed_end"
                    and capability in {"enumeration_verified", "range_verified"}
                    else "bounded"
                )
                db.execute(
                    "UPDATE watches SET scan_state_json=NULL,last_scan_upper=?,last_success_at=?,coverage_state=?,failure_count=0,error_code=NULL,error_since=NULL,updated_at=? WHERE id=?",
                    (scan["upper"], now, coverage, now, watch_id),
                )
                resolve_watch_incidents(db, watch_id, now)
            return continues

    def failure(self, watch_id, generation, error):
        now = int(self.instance.clock())
        with self.instance.transaction("result") as (db, _):
            try:
                w = active(db, watch_id, generation)
            except LurkerError:
                return
            count = w["failure_count"] + 1
            since = w["error_since"] if w["error_since"] is not None else now
            db.execute(
                "UPDATE watches SET failure_count=?,error_code=?,error_since=?,coverage_state='degraded',updated_at=? WHERE id=?",
                (count, error.code, since, now, watch_id),
            )
            if error.code in {
                "PAGE_PROTOCOL_INVALID",
                "PAGE_IDENTITY_INVALID",
                "CURSOR_LOOP",
                "SCAN_STATE_TOO_LARGE",
            } or (count >= 3 and now - since >= 7200):
                incident(db, error.code, watch_id, now)

    def change_variant(self, watch_id, variant):
        now = int(self.instance.clock())
        with self.instance.transaction() as (db, settings):
            w = active(db, watch_id)
            require(w["platform"] == "douyin" and variant in {"normal", "lite"}, "SOURCE_VARIANT_INVALID")
            if w["source_variant"] == variant:
                return w
            gaps = w["coverage_gaps_json"]
            if w["scan_state_json"]:
                scan = parse_json(w["scan_state_json"])
                gaps = add_gap(gaps, scan["lower"], scan["upper"], "source_variant_changed")
            db.execute(
                "UPDATE watches SET source_variant=?,scan_state_json=NULL,coverage_state='degraded',coverage_gaps_json=?,next_check_at=?,updated_at=? WHERE id=?",
                (variant, gaps, next_slot(now, settings), now, watch_id),
            )
            return watched(db, watch_id)
