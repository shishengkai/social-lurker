from datetime import datetime
from zoneinfo import ZoneInfo

from .errors import LurkerError, require
from .util import digest, now, token


class Notifications:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def get(self, notification_id):
        row = self.store.one("SELECT * FROM notifications WHERE id=?", (notification_id,))
        require(row, "NOT_FOUND", "通知不存在")
        return row

    def eligible(self, row):
        if row["account_id"]:
            account = self.store.account(row["account_id"])
            return account["tracking_state"] == "active" and account["watch_epoch"] == row["watch_epoch"]
        return (
            bool(self.store.one("SELECT id FROM accounts WHERE tracking_state='active' LIMIT 1"))
            and row["resolved_at"] is None
        )

    def body(self, row):
        if row["kind"] == "work":
            work = self.store.work(row["work_id"])
            account = self.store.account(work["account_id"])
            require(
                work["processing_state"] in ("ready", "no_speech"), "CONTENT_NOT_READY", "校对全文尚未就绪"
            )
            date = (
                datetime.fromtimestamp(work["published_at"], ZoneInfo(self.settings["timezone"])).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if work["published_at"]
                else "发布时间未知"
            )
            text = (
                work["transcript_text"] if work["processing_state"] == "ready" else "本作品未检测到口播内容。"
            )
            require(isinstance(text, str), "CONTENT_NOT_READY", "校对全文缺失")
            return f"{account['display_name']}\n{work['title'] or '未提供标题'}\n{date}\n{work['source_url'] or '来源链接暂不可取得'}\n\n{text}"
        if row["kind"] == "history_summary":
            run = self.store.run(row["run_id"])
            require(
                run["enumeration_complete"] and run["state"] in ("completed", "completed_with_errors"),
                "CONTENT_NOT_READY",
                "历史收集尚未结束",
            )
            counts = self.store.counts(run["id"])
            labels = {
                "acquired": "新增校对完成",
                "reused": "已有复用",
                "no_speech": "无语音",
                "failed": "失败",
                "unavailable": "不可获取",
                "skipped": "跳过",
            }
            lines = [f"{self.store.account(run['account_id'])['display_name']}：历史收集结束"]
            lines.extend(f"{label}：{counts.get(key, 0)}" for key, label in labels.items())
            lines.extend((f"合计：{sum(counts.values())}", f"保存位置：{self.store.path}"))
            return "\n".join(lines)
        return f"盯梢者需要处理：{row['incident_code']}\n范围：{row['incident_scope']}\n资料已保留，请查看 status 并处理后继续。"

    def list(self):
        return self.store.all(
            "SELECT id,kind,work_id,run_id,state,last_error_code FROM notifications WHERE state NOT IN ('sent','canceled') ORDER BY id"
        )

    def claim(self, notification_id):
        require(
            self.settings["delivery"]["verified"],
            "DELIVERY_UNVERIFIED",
            "请先在 Grok Bot 验证原生发送和回执能力",
        )
        with self.store.tx():
            row = self.get(notification_id)
            require(self.eligible(row), "NOTIFICATION_CANCELED", "账号已停止或通知属于旧周期")
            require(
                row["state"] in ("pending", "retry_wait")
                and (not row["next_attempt_at"] or row["next_attempt_at"] <= now()),
                "DISPATCH_NOT_READY",
                "通知不可领取；结果未知时须先核对",
            )
            body = self.body(row)
            limit = self.settings["delivery"]["max_chars"]
            if limit is not None and len(body) > limit:
                self.store.execute(
                    "UPDATE notifications SET state='blocked',last_error_code='CONTENT_TOO_LONG',updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
                if row["kind"] != "incident":
                    self.store.incident(
                        "CONTENT_TOO_LONG", f"account:{row['account_id']}:delivery", row["account_id"]
                    )
                return {
                    "blocked": True,
                    "error_code": "CONTENT_TOO_LONG",
                    "characters": len(body),
                    "limit": limit,
                }
            dispatch = token()
            self.store.execute(
                "UPDATE notifications SET state='sending',dispatch_token=?,attempt_count=attempt_count+1,updated_at=? WHERE id=?",
                (dispatch, now(), row["id"]),
            )
        return {
            "notification_id": row["id"],
            "dispatch_token": dispatch,
            "body_hash": digest(body),
            "next": "render immediately before native delivery",
        }

    def render(self, notification_id, dispatch_token):
        row = self.get(notification_id)
        require(
            row["state"] == "sending"
            and row["dispatch_token"] == dispatch_token
            and now() - row["updated_at"] < 600,
            "DISPATCH_NOT_READY",
            "通知领取已失效",
        )
        require(self.eligible(row), "NOTIFICATION_CANCELED", "账号已停止或通知属于旧周期")
        body = self.body(row)
        return {
            "notification_id": row["id"],
            "body": body,
            "body_hash": digest(body),
            "characters": len(body),
        }

    def ack(self, notification_id, dispatch_token, evidence):
        with self.store.tx():
            row = self.get(notification_id)
            require(row["dispatch_token"] == dispatch_token, "STALE_OWNER", "发送凭证不匹配")
            require(
                row["state"] in ("sending", "unknown", "sent"), "DISPATCH_NOT_READY", "发送状态不允许确认"
            )
            body = self.body(row)
            require(
                isinstance(evidence, dict)
                and isinstance(evidence.get("provider_message_id"), str)
                and bool(evidence["provider_message_id"]),
                "DELIVERY_EVIDENCE_REQUIRED",
                "需要原生发送返回的消息 ID",
            )
            require(
                evidence.get("body_hash") == digest(body),
                "DELIVERY_EVIDENCE_REQUIRED",
                "发送正文 hash 不匹配",
            )
            if "readback_text" in evidence:
                require(evidence["readback_text"] == body, "DELIVERY_MISMATCH", "读回正文不完整或已被改写")
            if row["state"] == "sent":
                require(
                    row["provider_message_id"] == evidence["provider_message_id"],
                    "DELIVERY_MISMATCH",
                    "消息回执不一致",
                )
                return {"sent": True}
            # Even when stop races after actual delivery, preserve the true delivery evidence.
            self.store.execute(
                "UPDATE notifications SET state='sent',provider_message_id=?,sent_at=?,updated_at=? WHERE id=?",
                (evidence["provider_message_id"], now(), now(), row["id"]),
            )
        return {"sent": True}

    def resolve(self, notification_id, action, payload):
        row = self.get(notification_id)
        require(row["state"] in ("unknown", "blocked", "sending"), "DISPATCH_NOT_READY", "该通知无需核对")
        if action == "sent":
            return self.ack(notification_id, row["dispatch_token"], payload.get("evidence"))
        if action == "resend":
            require(
                payload.get("accept_duplicate_risk") is True and self.eligible(row),
                "DELIVERY_EVIDENCE_REQUIRED",
                "重发须明确接受重复风险，且账号仍在当前周期",
            )
            self.store.execute(
                "UPDATE notifications SET state='pending',dispatch_token=NULL,last_error_code=NULL,updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        elif action in ("unknown", "too_long"):
            state, code = (
                ("unknown", "DELIVERY_UNKNOWN") if action == "unknown" else ("blocked", "CONTENT_TOO_LONG")
            )
            self.store.execute(
                "UPDATE notifications SET state=?,last_error_code=?,updated_at=? WHERE id=?",
                (state, code, now(), row["id"]),
            )
            if action == "too_long" and row["kind"] != "incident":
                self.store.incident(code, f"account:{row['account_id']}:delivery", row["account_id"])
        else:
            raise LurkerError("INVALID_REQUEST", "不支持的通知核对操作")
        return {"state": self.get(notification_id)["state"]}
