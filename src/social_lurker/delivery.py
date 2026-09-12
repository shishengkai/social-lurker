"""One work, one frozen send permit. Unknown is a fact, never a retry signal."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import automatic_gate
from .errors import LurkerError, require
from .schedule import allowed
from .state import array, incident
from .store import source_url
from .util import atomic_json, canonical, clean_text, digest, ident, lock, public_url


def escaped(value):
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", value)


def format_payload(row, settings):
    title = clean_text(row["title"], 4096)
    title = (title[:120] + "…") if len(title) > 120 else title
    title = title or "（未提供标题）"
    author = escaped(clean_text(row["author_name"], 512))
    platform = "抖音" if row["platform"] == "douyin" else "视频号"
    published = datetime.fromtimestamp(row["published_at"], ZoneInfo(settings["timezone"])).strftime(
        "%Y-%m-%d %H:%M"
    )
    link = source_url(row["platform"], row["source_url"])
    require(link, "SOURCE_LINK_INVALID")
    text = f"**{author} · {platform}**\n\n{escaped(title)}\n\n发布时间：{published}（{settings['timezone']}）\n\n{link}"
    evidence = settings["host"].get("evidence_ref") or {}
    host = evidence.get("host", {})
    images = []
    if host.get("images_verified") is True and public_url(row.get("cover_url")):
        images = [{"url": row["cover_url"], "alt": "作品封面"}]
    result = {"text": text, "images": images}
    return check_length(result, settings)


def check_length(result, settings):
    host = (settings["host"]["evidence_ref"] or {}).get("host", {})
    limit = host.get("max_message_length")
    unit = host.get("length_unit")
    require(
        type(limit) is int and limit > 0 and unit in {"unicode", "utf8", "utf16"},
        "HOST_LENGTH_UNVERIFIED",
        "尚未核验宿主消息长度限制",
    )
    raw = canonical(result)
    length = (
        len(raw)
        if unit == "unicode"
        else (len(raw.encode("utf-8")) if unit == "utf8" else len(raw.encode("utf-16-le")) // 2)
    )
    require(length <= limit, "MESSAGE_TOO_LONG", "作品消息超出宿主限制，等待核对")
    return result


def validate_receipt(receipt):
    required = {"instance_id", "kind", "object_id", "attempt_id", "payload_hash", "result"}
    require(
        isinstance(receipt, dict)
        and required <= set(receipt) <= required | {"provider_message_id", "sent_at", "evidence"},
        "RECEIPT_INVALID",
    )
    require(
        receipt["kind"] in {"update", "incident"} and receipt["result"] in {"sent", "not_sent", "unknown"},
        "RECEIPT_INVALID",
    )
    for name in ("instance_id", "object_id", "attempt_id", "payload_hash"):
        require(isinstance(receipt[name], str) and 0 < len(receipt[name]) <= 512, "RECEIPT_INVALID")
    require(re.fullmatch(r"[0-9a-f]{64}", receipt["payload_hash"]), "RECEIPT_INVALID")
    if receipt["result"] == "sent":
        require(
            isinstance(receipt.get("provider_message_id"), str)
            and 0 < len(receipt["provider_message_id"]) <= 512,
            "RECEIPT_EVIDENCE_REQUIRED",
        )
        require(type(receipt.get("sent_at")) is int and receipt["sent_at"] > 0, "RECEIPT_EVIDENCE_REQUIRED")
    if receipt["result"] == "not_sent":
        evidence = receipt.get("evidence")
        require(
            isinstance(evidence, dict) and set(evidence) == {"type", "reference", "attempt_id"},
            "RECEIPT_EVIDENCE_REQUIRED",
        )
        require(
            evidence["type"] in {"provider_rejected", "provider_lookup_not_delivered"}
            and evidence["attempt_id"] == receipt["attempt_id"],
            "RECEIPT_EVIDENCE_REQUIRED",
        )
        require(
            isinstance(evidence["reference"], str) and 0 < len(evidence["reference"]) <= 512,
            "RECEIPT_EVIDENCE_REQUIRED",
        )
    require(len(canonical(receipt).encode()) <= 8192, "RECEIPT_INVALID")
    return receipt


class Delivery:
    def __init__(self, instance):
        self.instance = instance

    def expire(self, db, now):
        rows = db.execute(
            "SELECT id FROM updates WHERE state='sending' AND send_started_at<=?", (now - 600,)
        ).fetchall()
        db.execute(
            "UPDATE updates SET state='unknown' WHERE state='sending' AND send_started_at<=?", (now - 600,)
        )
        for row in rows:
            incident(db, "DELIVERY_UNKNOWN", row[0], now)
        notices = array(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
        for item in notices:
            if item["state"] == "sending" and item["send_started_at"] <= now - 600:
                item["state"] = "unknown"
        db.execute("UPDATE runtime SET incidents_json=?", (canonical(notices),))

    def next(self, *, foreground_test=False, automatic=False):
        now = int(self.instance.clock())
        with self.instance.transaction("permit") as (db, settings):
            if automatic:
                require(allowed(now, settings), "QUIET_HOURS")
                automatic_gate(settings)
            self.expire(db, now)
            require(
                settings["host"]["delivery_verified_at"] is not None or foreground_test,
                "HOST_DELIVERY_UNVERIFIED",
                "宿主发送与回执尚待验证",
            )
            rows = db.execute(
                "SELECT u.* FROM updates u JOIN watches w ON w.id=u.watch_id WHERE u.state='queued' AND w.status='active' AND u.eligible_generation=w.generation AND (u.next_attempt_at IS NULL OR u.next_attempt_at<=?) ORDER BY u.published_at,u.platform,w.author_id,u.work_id",
                (now,),
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                if settings["host"]["delivery_verified_at"] is None and row["reason"] != "test":
                    continue
                try:
                    payload = format_payload(row, settings)
                except LurkerError as error:
                    if error.code == "HOST_LENGTH_UNVERIFIED":
                        raise
                    db.execute(
                        "UPDATE updates SET state='blocked',error_code=?,next_attempt_at=NULL WHERE id=?",
                        (error.code, row["id"]),
                    )
                    incident(db, error.code, row["id"], now)
                    continue
                aid = ident()
                encoded = canonical(payload)
                hashed = digest(encoded)
                db.execute(
                    "UPDATE updates SET state='sending',attempt_id=?,payload=?,payload_hash=?,send_started_at=?,provider_message_id=NULL,sent_at=NULL WHERE id=?",
                    (aid, encoded, hashed, now, row["id"]),
                )
                return dict(
                    instance_id=settings["instance_id"],
                    kind="update",
                    object_id=row["id"],
                    attempt_id=aid,
                    payload=payload,
                    payload_hash=hashed,
                )
            if foreground_test and settings["host"]["delivery_verified_at"] is None:
                return None
            notices = array(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
            item = next((n for n in notices if n["state"] == "pending"), None)
            if item:
                payload = {"text": f"盯梢者需要处理：{item['code']}。请查看实例状态。", "images": []}
                check_length(payload, settings)
                aid = ident()
                encoded = canonical(payload)
                hashed = digest(encoded)
                item.update(
                    state="sending", attempt_id=aid, payload=encoded, payload_hash=hashed, send_started_at=now
                )
                db.execute("UPDATE runtime SET incidents_json=?", (canonical(notices),))
                return dict(
                    instance_id=settings["instance_id"],
                    kind="incident",
                    object_id=item["id"],
                    attempt_id=aid,
                    payload=payload,
                    payload_hash=hashed,
                )
            return None

    def _apply(self, db, settings, receipt):
        validate_receipt(receipt)
        require(receipt["instance_id"] == settings["instance_id"], "INSTANCE_MISMATCH")
        notices = None
        if receipt["kind"] == "update":
            value = db.execute("SELECT * FROM updates WHERE id=?", (receipt["object_id"],)).fetchone()
            require(value is not None, "RECEIPT_OBJECT_UNKNOWN")
            row = dict(value)
        else:
            notices = array(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
            row = next((n for n in notices if n["id"] == receipt["object_id"]), None)
            require(row is not None, "RECEIPT_OBJECT_UNKNOWN")
        require(
            row.get("attempt_id") == receipt["attempt_id"]
            and row.get("payload_hash") == receipt["payload_hash"],
            "RECEIPT_ATTEMPT_MISMATCH",
        )
        result = receipt["result"]
        if row["state"] == "sent":
            require(
                result in {"sent", "unknown"}
                and (result == "unknown" or row["provider_message_id"] == receipt["provider_message_id"]),
                "RECEIPT_CONFLICT",
            )
            return {"state": "sent", "duplicate": True}
        if row["state"] in {"queued", "cancelled", "pending"}:
            require(result == "not_sent", "RECEIPT_CONFLICT")
            return {"state": row["state"], "duplicate": True}
        require(row["state"] in {"sending", "unknown"}, "RECEIPT_CONFLICT")
        values = {}
        if result == "sent":
            require(receipt["sent_at"] <= int(self.instance.clock()) + 300, "RECEIPT_INVALID")
            values = {"state": "sent", "provider_message_id": receipt["provider_message_id"]}
            values["sent_at" if receipt["kind"] == "update" else "last_sent_at"] = receipt["sent_at"]
        elif result == "unknown":
            values = {"state": "unknown"}
        elif receipt["kind"] == "incident":
            values = {"state": "pending"}
        else:
            watch = db.execute("SELECT * FROM watches WHERE id=?", (row["watch_id"],)).fetchone()
            same = watch and watch["status"] == "active" and watch["generation"] == row["eligible_generation"]
            values = {
                "state": "queued" if same else "cancelled",
                "next_attempt_at": int(self.instance.clock()) + 30 if same else None,
            }
        if receipt["kind"] == "update":
            from .store import update_row

            update_row(db, row["id"], values)
            if result == "unknown":
                incident(db, "DELIVERY_UNKNOWN", row["id"], int(self.instance.clock()))
        else:
            row.update(values)
            db.execute("UPDATE runtime SET incidents_json=?", (canonical(notices),))
        return {"state": values["state"], "duplicate": False}

    def report(self, receipt):
        validate_receipt(receipt)
        with lock(self.instance.root, "state.lock"):
            settings = self.instance.load()
            plan = self.instance.plan()
            require(receipt["instance_id"] == settings["instance_id"], "INSTANCE_MISMATCH")
            frozen = plan and plan["stage"] != "prepared" and not plan["business_writes_open"]
            if frozen:
                encoded = canonical(receipt)
                pending = plan["pending_receipts"]
                if not any(canonical(item["receipt"]) == encoded for item in pending):
                    pending.append({"receipt": receipt, "state": "pending"})
                require(len(canonical(plan).encode()) <= 8 * 1024 * 1024, "RECEIPT_INBOX_FULL")
                atomic_json(self.instance.path("maintenance.json"), plan)
                return {"accepted": True, "state": "pending_registration"}
            settings = self.instance.gate("result")
            with self.instance.connection(settings) as db:
                return self._apply(db, settings, receipt)

    def replay_locked(self, plan, settings):
        """Called under state.lock by the selected version after commit/rollback."""
        for item in plan["pending_receipts"]:
            if item["state"] != "pending":
                continue
            try:
                with self.instance.connection(settings) as db:
                    outcome = self._apply(db, settings, item["receipt"])
                # Intentional DB-before-journal boundary; replay after a crash is idempotent.
                item.update(state="applied", result=outcome)
            except LurkerError as error:
                item.update(state="rejected", code=error.code)
            atomic_json(self.instance.path("maintenance.json"), plan)
        return plan

    def skip(self, ids):
        require(
            isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids), "EXPLICIT_RANGE_REQUIRED"
        )
        with self.instance.transaction() as (db, _):
            for uid in ids:
                require(
                    db.execute("SELECT 1 FROM updates WHERE id=? AND state='queued'", (uid,)).fetchone(),
                    "UPDATE_CHANGED",
                )
            for uid in ids:
                db.execute("UPDATE updates SET state='cancelled',next_attempt_at=NULL WHERE id=?", (uid,))
        return {"cancelled": ids}
