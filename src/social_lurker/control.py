import json
from datetime import datetime

from .errors import LurkerError, require
from .util import digest, file_lock, json_text, now, write_json


class Journal:
    """One bounded file, no payload bodies, credential values or credential digests."""

    def __init__(self, instance):
        self.path = instance.maintenance / "control.json"
        self.lock = instance.maintenance / "control.lock"

    def perform(self, request, command, summary, operation, prepare=lambda: {}):
        created = request.get("created_at")
        try:
            created = (
                datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if isinstance(created, str)
                else float(created)
            )
        except (TypeError, ValueError):
            raise LurkerError("REQUEST_TIME_REQUIRED", "变更请求需包含 created_at UTC 时间") from None
        require(
            now() - 14 * 86400 <= created <= now() + 300,
            "REQUEST_EXPIRED",
            "请求过期或时间无效，请使用新的请求 ID",
        )
        signature = digest(json_text({"command": command, "summary": summary}))
        with file_lock(self.lock):
            try:
                entries = json.loads(self.path.read_text()) if self.path.exists() else {}
            except (OSError, ValueError):
                raise LurkerError("CONTROL_JOURNAL_INVALID", "控制日志不可读取，请先核对") from None
            entries = {
                key: entry for key, entry in entries.items() if now() - entry["created_at"] < 14 * 86400
            }
            previous = entries.get(request["request_id"])
            if previous:
                require(
                    previous["signature"] == signature, "REQUEST_CONFLICT", "相同 request_id 对应不同操作"
                )
                if previous["state"] == "complete":
                    return previous["result"]
                intent = previous["intent"]
            else:
                intent = prepare()
                entries[request["request_id"]] = {
                    "created_at": created,
                    "signature": signature,
                    "state": "intent",
                    "intent": intent,
                }
                write_json(self.path, entries)
            result = operation(intent)
            entries[request["request_id"]].update(state="complete", result=result)
            write_json(self.path, entries)
            return result
