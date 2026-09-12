import json
import re
import unicodedata

from .errors import LurkerError, require
from .util import digest, json_text, now, token


def segments(text, limit=6000):
    result = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundaries = [m.end() + start for m in re.finditer(r"\n|[。！？.!?](?:\s|$)", text[start:end])]
            if boundaries:
                end = boundaries[-1]
        result.append({"start": start, "end": end, "done": False, "edits": []})
        start = end
    return result


def punctuation(text):
    return all(c.isspace() or unicodedata.category(c).startswith("P") for c in text)


def validate_edits(raw, segment, edits):
    require(isinstance(edits, list), "PROOFREAD_INVALID", "edits 必须为数组")
    validated = []
    previous_end = segment["start"]
    previous_start = None
    for edit in edits:
        require(
            isinstance(edit, dict) and set(edit) == {"start", "end", "old", "replacement"},
            "PROOFREAD_INVALID",
            "每个 edit 需 start/end/old/replacement",
        )
        start, end, old, replacement = (edit[k] for k in ("start", "end", "old", "replacement"))
        require(
            type(start) is int and type(end) is int and isinstance(old, str) and isinstance(replacement, str),
            "PROOFREAD_INVALID",
            "修改字段类型无效",
        )
        require(
            segment["start"] <= start <= end <= segment["end"]
            and start >= previous_end
            and start != previous_start,
            "PROOFREAD_INVALID",
            "修改越界、重叠或未按原文顺序排列",
        )
        require(raw[start:end] == old, "PROOFREAD_INVALID", "原文片段不匹配")
        if not old:
            require(
                bool(replacement) and punctuation(replacement), "PROOFREAD_INVALID", "插入仅允许标点或空白"
            )
        elif not replacement:
            require(punctuation(old), "PROOFREAD_INVALID", "不得删除正文词语")
        else:
            require(
                punctuation(old) == punctuation(replacement),
                "PROOFREAD_INVALID",
                "不能把正文替换为纯标点，或借替换标点插入正文",
            )
            require(max(len(old), len(replacement)) <= 32, "PROOFREAD_INVALID", "局部替换不可超过 32 字")
        require(len(replacement) <= 64, "PROOFREAD_INVALID", "标点插入过长")
        validated.append(edit)
        previous_end, previous_start = end, start
    return validated


def apply_edits(raw, edits):
    output = []
    offset = 0
    for edit in edits:
        output.extend((raw[offset : edit["start"]], edit["replacement"]))
        offset = edit["end"]
    output.append(raw[offset:])
    return "".join(output)


class Proofreader:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings

    def next(self, work_id=None):
        with self.store.tx():
            params = () if work_id is None else (work_id,)
            work = self.store.one(
                "SELECT * FROM works WHERE processing_state IN ('pending_proofread','proofreading')"
                + (" AND id=?" if work_id is not None else "")
                + " ORDER BY id LIMIT 1",
                params,
            )
            if not work:
                return None
            raw = work["raw_transcript_text"]
            require(
                isinstance(raw, str) and digest(raw) == work["raw_text_hash"],
                "RAW_HASH_MISMATCH",
                "原稿完整性校验失败",
            )
            progress = json.loads(work["proofread_progress"]) if work["proofread_progress"] else segments(raw)
            pending = next((s for s in progress if not s["done"]), None)
            require(pending is not None, "PROOFREAD_INVALID", "缺少待校对分段")
            owner = work["owner_token"] if work["lease_until"] and work["lease_until"] > now() else token()
            self.store.execute(
                """UPDATE works SET processing_state='proofreading',owner_token=?,lease_until=?,
              proofread_progress=?,updated_at=? WHERE id=?""",
                (
                    owner,
                    now() + self.settings["execution"]["lease_seconds"],
                    json_text(progress),
                    now(),
                    work["id"],
                ),
            )
            start, end = pending["start"], pending["end"]
            return {
                "work_id": work["id"],
                "owner_token": owner,
                "raw_hash": work["raw_text_hash"],
                "segment": {"start": start, "end": end, "text": raw[start:end]},
                "readonly_before": raw[max(0, start - 300) : start],
                "readonly_after": raw[end : end + 300],
                "index_unit": "Unicode code points; absolute offsets in raw text",
                "lease_seconds": self.settings["execution"]["lease_seconds"],
            }

    def submit(self, payload):
        work = self.store.work(payload["work_id"])
        require(
            work["processing_state"] == "proofreading"
            and work["owner_token"] == payload.get("owner_token")
            and work["lease_until"]
            and work["lease_until"] > now(),
            "STALE_OWNER",
            "校对领取权已过期，请重新领取",
        )
        require(payload.get("raw_hash") == work["raw_text_hash"], "RAW_HASH_MISMATCH", "原稿 hash 不匹配")
        progress = json.loads(work["proofread_progress"])
        segment = next(s for s in progress if not s["done"])
        require(
            payload.get("start") == segment["start"] and payload.get("end") == segment["end"],
            "PROOFREAD_INVALID",
            "提交的分段范围不匹配",
        )
        try:
            edits = validate_edits(work["raw_transcript_text"], segment, payload.get("edits"))
        except LurkerError as error:
            counts = json.loads(work["stage_attempts"])
            counts["proofread"] = counts.get("proofread", 0) + 1
            with self.store.tx():
                self.store.execute(
                    "UPDATE works SET stage_attempts=?,attempt_count=attempt_count+1 WHERE id=?",
                    (json_text(counts), work["id"]),
                )
                if counts["proofread"] >= 3:
                    self.store.execute(
                        "UPDATE works SET processing_state='blocked',last_error_code='PROOFREAD_FAILED',owner_token=NULL WHERE id=?",
                        (work["id"],),
                    )
                    self.store.execute(
                        "UPDATE collection_items SET result='blocked',error_code='PROOFREAD_FAILED' WHERE work_id=? AND result='pending'",
                        (work["id"],),
                    )
                    self.store.incident(
                        "PROOFREAD_FAILED", f"account:{work['account_id']}:proofread", work["account_id"]
                    )
            self.store.finish_runs()
            raise error
        segment.update(done=True, edits=edits)
        # CLI holds the instance lock throughout: owner is still checked inside final write transaction.
        if all(s["done"] for s in progress):
            final = apply_edits(work["raw_transcript_text"], [edit for s in progress for edit in s["edits"]])
            self.store.finish_work(
                work["id"], "ready", owner=work["owner_token"], final=final, model=payload.get("model")
            )
            return {"work_id": work["id"], "complete": True, "text_hash": digest(final)}
        with self.store.tx():
            self.store.execute(
                "UPDATE works SET proofread_progress=?,lease_until=?,updated_at=? WHERE id=? AND owner_token=?",
                (
                    json_text(progress),
                    now() + self.settings["execution"]["lease_seconds"],
                    now(),
                    work["id"],
                    work["owner_token"],
                ),
            )
        return {"work_id": work["id"], "complete": False}
