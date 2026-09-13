"""Small bounded runtime JSON contracts, independent from delivery outcomes."""

from .errors import require
from .util import canonical, digest, ident, parse_json


def array(raw, limit=256 * 1024):
    result = parse_json(raw, limit)
    require(isinstance(result, list), "STATE_INVALID")
    return result


def holds(db):
    result = array(db.execute("SELECT api_holds_json FROM runtime").fetchone()[0])
    from .sources import ENDPOINTS

    for h in result:
        require(
            isinstance(h, dict) and set(h) == {"hold_id", "reason", "scope", "endpoint", "since"},
            "STATE_INVALID",
        )
        require(
            h["scope"] in {"instance_tikhub", "endpoint"}
            and h["endpoint"] in ENDPOINTS
            and h["reason"] in {"API_AUTH", "API_PERMISSION", "API_QUOTA"},
            "STATE_INVALID",
        )
    return result


def applicable(hold, endpoint):
    return hold["scope"] == "instance_tikhub" or hold["endpoint"] == endpoint


def add_hold(db, endpoint, reason, now):
    rows = holds(db)
    scope = "endpoint" if reason == "API_PERMISSION" else "instance_tikhub"
    if not any(h["reason"] == reason and h["scope"] == scope and h["endpoint"] == endpoint for h in rows):
        rows.append(dict(hold_id=ident(), reason=reason, scope=scope, endpoint=endpoint, since=now))
    db.execute("UPDATE runtime SET api_holds_json=?,updated_at=?", (canonical(rows), now))
    incident(db, reason, endpoint, now)


def resolve_watch_incidents(db, watch_id, recovered_at):
    """A completed author scan resolves its faults, never API holds or send outcomes."""
    rows = array(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
    changed = False
    for item in rows:
        if item.get("resolved_at") is not None or item["key"] != digest(item["code"] + ":" + watch_id):
            continue
        if item["created_at"] > recovered_at:
            continue
        item["resolved_at"] = recovered_at
        if item["state"] == "pending":
            item["state"] = "resolved"
        changed = True
    if changed:
        db.execute("UPDATE runtime SET incidents_json=?", (canonical(rows),))


def reconcile_watch_incidents(db):
    """Reconcile older packages' pending faults using persisted successful scans."""
    rows = array(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
    for watch in db.execute("SELECT id,last_success_at FROM watches WHERE error_code IS NULL"):
        recovered_at = watch["last_success_at"]
        if recovered_at is not None and any(
            item.get("resolved_at") is None
            and item["created_at"] < recovered_at
            and item["key"] == digest(item["code"] + ":" + watch["id"])
            for item in rows
        ):
            resolve_watch_incidents(db, watch["id"], recovered_at)


def incident(db, code, subject, now):
    rows = array(db.execute("SELECT incidents_json FROM runtime").fetchone()[0])
    key = digest(code + ":" + subject)
    current = next((r for r in reversed(rows) if r["key"] == key and r.get("resolved_at") is None), None)
    if current and current["state"] in {"sending", "unknown"}:
        return
    if current and current.get("last_sent_at") is not None and now - current["last_sent_at"] < 86400:
        return
    if current and current["state"] == "pending":
        return
    if len(rows) >= 32:
        removable = next(
            (
                r
                for r in rows
                if r["state"] in {"resolved", "sent"}
                and r.get("resolved_at") is not None
                and now - r["resolved_at"] >= 86400
                and (r.get("last_sent_at") is None or now - r["last_sent_at"] >= 86400)
            ),
            None,
        )
        if removable is None:
            return
        rows.remove(removable)
    rows.append(
        dict(
            id=ident(),
            key=key,
            code=code,
            state="pending",
            created_at=now,
            last_sent_at=None,
            attempt_id=None,
            payload=None,
            payload_hash=None,
            send_started_at=None,
            provider_message_id=None,
            resolved_at=None,
        )
    )
    db.execute("UPDATE runtime SET incidents_json=?,updated_at=?", (canonical(rows), now))


def add_gap(raw, start, end, reason):
    gaps = array(raw)
    gaps.append({"from": start, "to": end, "reason": reason})
    gaps.sort(key=lambda g: g["from"])
    merged = []
    for g in gaps:
        if merged and g["from"] <= merged[-1]["to"] and g["reason"] == merged[-1]["reason"]:
            merged[-1]["to"] = max(g["to"], merged[-1]["to"])
        else:
            merged.append(g)
    if len(merged) > 20:
        older = merged[:-19]
        merged = [
            {"from": older[0]["from"], "to": max(g["to"] for g in older), "reason": "coarsened_gap"}
        ] + merged[-19:]
    return canonical(merged)
