from .errors import require
from .store import OPEN_RUNS
from .util import now, time_range


def prepare_collection(store, account_id, payload, request_id, timezone):
    previous = store.one("SELECT * FROM collection_runs WHERE request_id=?", (request_id,))
    if previous:
        return {"run_id": previous["id"]}
    account = store.account(account_id)
    require(account["tracking_state"] == "active", "ACCOUNT_STOPPED", "停止的账号不能新建历史任务")
    unfinished = store.one(
        f"SELECT id FROM collection_runs WHERE account_id=? AND kind='history' AND state IN {OPEN_RUNS}",
        (account_id,),
    )
    require(
        not unfinished or payload.get("mode") in ("append", "replace"),
        "HISTORY_SCOPE_EXISTS",
        "已有未结束历史任务，请明确追加或替换",
    )
    scope = payload.get("scope")
    require(scope in ("time", "count", "all"), "INVALID_RANGE", "历史范围应为 time/count/all")
    end = now()
    start, count = None, None
    if scope == "time":
        start, end = time_range(end, payload.get("amount"), payload.get("unit"), timezone)
    if scope == "count":
        count = payload.get("count")
        require(type(count) is int and count > 0, "INVALID_RANGE", "作品条数必须为正整数")
    with store.tx():
        if unfinished and payload.get("mode") == "replace":
            # Replacing a user scope only withdraws this run; shared/in-flight work remains registered.
            store.execute(
                "UPDATE collection_runs SET state='canceled',finished_at=? WHERE id=?",
                (now(), unfinished["id"]),
            )
        run = store.create_run(
            account, "history", request_id, scope, start=start, end=end, count=count, confirmed=False
        )
    return {"run_id": run["id"], "phase": "metadata_only", "fees": "unknown", "asr_started": False}


def collection_plan(store, run_id):
    run = store.run(run_id)
    return {
        "run_id": run_id,
        "scope": run["scope_type"],
        "range_start": run["range_start"],
        "range_end": run["range_end"],
        "requested_count": run["requested_count"],
        "count": run["reported_total"],
        "total_basis": run["total_basis"],
        "unknown_publication_count": store.one(
            "SELECT count(*) n FROM works w JOIN collection_items i ON i.work_id=w.id WHERE i.run_id=? AND w.published_at IS NULL",
            (run_id,),
        )["n"],
        "enumeration_complete": bool(run["enumeration_complete"]),
        "confirmed": bool(run["plan_confirmed"]),
        "estimated_asr_duration_seconds": None,
        "estimated_cost": None,
        "state": run["state"],
        "error_code": run["last_error_code"],
        "counts": store.counts(run_id),
    }


def confirm_collection(store, run_id, payload):
    with store.tx():
        run = store.run(run_id)
        account = store.account(run["account_id"])
        require(
            account["tracking_state"] == "active" and account["watch_epoch"] == run["watch_epoch"],
            "ACCOUNT_STOPPED",
            "当前账号或盯梢周期已变化",
        )
        require(
            run["state"] in OPEN_RUNS and run["enumeration_complete"],
            "ENUMERATION_UNCERTAIN",
            "元信息范围尚未清点完",
        )
        require(
            payload.get("acknowledged_count") == run["reported_total"]
            and payload.get("accept_service_costs") is True,
            "HISTORY_CONFIRMATION_REQUIRED",
            "需展示并确认本次实际数量及服务计费口径",
        )
        store.execute("UPDATE collection_runs SET plan_confirmed=1,state='running' WHERE id=?", (run_id,))
    store.finish_runs()
    return {"run_id": run_id, "confirmed": True}


def retry(store, payload, request_id):
    if payload.get("run_id"):
        original = store.run(payload["run_id"])
        previous = store.one("SELECT id FROM collection_runs WHERE request_id=?", (request_id,))
        if previous:
            return {"run_id": previous["id"]}
        if original["state"] in ("completed", "completed_with_errors", "failed"):
            ids = payload.get("work_ids")
            require(
                isinstance(ids, list) and ids and all(type(x) is int for x in ids),
                "INVALID_REQUEST",
                "请指定重试作品 ID",
            )
            for wid in ids:
                item = store.one(
                    "SELECT * FROM collection_items WHERE run_id=? AND work_id=?", (original["id"], wid)
                )
                require(
                    item and item["result"] in ("failed", "unavailable", "skipped"),
                    "INVALID_REQUEST",
                    "只能选择原批次失败范围",
                )
            with store.tx():
                account = store.account(original["account_id"])
                require(account["tracking_state"] == "active", "ACCOUNT_STOPPED", "停止后不能新建重试批次")
                run = store.create_run(
                    account,
                    "history",
                    request_id,
                    "selected",
                    end=original["range_end"],
                    parent=original["id"],
                )
                store.execute(
                    "UPDATE collection_runs SET state='running',started_at=?,enumeration_complete=1,reported_total=?,total_basis='selected_ids' WHERE id=?",
                    (now(), len(ids), run["id"]),
                )
                for wid in ids:
                    work = store.work(wid)
                    if work["processing_state"] in ("ready", "no_speech"):
                        result = "reused" if work["processing_state"] == "ready" else "no_speech"
                    else:
                        reset_work(store, work, payload)
                        result = "pending"
                    store.execute(
                        "INSERT INTO collection_items(run_id,work_id,result,created_at) VALUES(?,?,?,?)",
                        (run["id"], wid, result, now()),
                    )
            store.finish_runs()
            return {"run_id": run["id"], "parent_run_id": original["id"]}
        with store.tx():
            require(original["state"] in ("blocked", "retry_wait"), "INVALID_REQUEST", "该任务无需重试")
            store.execute(
                "UPDATE collection_runs SET state='running',attempt_count=0,last_error_code=NULL,next_attempt_at=NULL,cursor=CASE WHEN enumeration_complete=0 THEN NULL ELSE cursor END WHERE id=?",
                (original["id"],),
            )
            for item in store.all(
                "SELECT w.* FROM works w JOIN collection_items i ON w.id=i.work_id WHERE i.run_id=? AND i.result='blocked'",
                (original["id"],),
            ):
                reset_work(store, item, payload)
        return {"run_id": original["id"]}
    with store.tx():
        work = store.work(payload["work_id"])
        # Closed historical summaries are immutable; retry through a selected child run instead.
        require(
            store.one(
                f"SELECT i.run_id FROM collection_items i JOIN collection_runs r ON r.id=i.run_id WHERE i.work_id=? AND r.state IN {OPEN_RUNS}",
                (work["id"],),
            ),
            "RETRY_RUN_REQUIRED",
            "原批次已结束，请通过 run_id 创建所选作品的重试批次",
        )
        reset_work(store, work, payload)
    return {"work_id": work["id"]}


def reset_work(store, work, payload):
    require(
        work["processing_state"] in ("blocked", "failed", "unavailable", "submit_unknown", "retry_wait"),
        "INVALID_REQUEST",
        "不能重置正在执行或已成功的作品",
    )
    if work["processing_state"] == "submit_unknown":
        if payload.get("external_job_id"):
            require(isinstance(payload["external_job_id"], str), "INVALID_REQUEST", "远端任务标识无效")
            store.execute(
                "UPDATE works SET external_job_id=?,asr_phase='submitted',asr_reviewed_at=?,processing_state='transcribing',execution_cycle=execution_cycle+1 WHERE id=?",
                (payload["external_job_id"], now(), work["id"]),
            )
        else:
            require(
                payload.get("accept_duplicate_charge_risk") is True,
                "ASR_RECONCILIATION_REQUIRED",
                "提交不明需绑定远端任务，或明确接受重复收费风险",
            )
            store.execute(
                "UPDATE works SET external_job_id=NULL,asr_phase=NULL,processing_state='pending',execution_cycle=execution_cycle+1 WHERE id=?",
                (work["id"],),
            )
    else:
        state = (
            "pending_proofread"
            if work["raw_transcript_text"]
            else ("transcribing" if work["external_job_id"] else "pending")
        )
        # A known remote job is always queried again, never silently resubmitted.
        store.execute(
            "UPDATE works SET processing_state=?,execution_cycle=execution_cycle+1 WHERE id=?",
            (state, work["id"]),
        )
        if work["last_error_code"] == "ASR_STALE":
            store.execute("UPDATE works SET asr_reviewed_at=? WHERE id=?", (now(), work["id"]))
    store.execute(
        "UPDATE works SET stage_attempts='{}',attempt_count=0,next_attempt_at=NULL,owner_token=NULL,lease_until=NULL,last_error_code=NULL,last_error_message=NULL WHERE id=?",
        (work["id"],),
    )
    store.execute(
        f"UPDATE collection_items SET result='pending',error_code=NULL,finished_at=NULL WHERE work_id=? AND result='blocked' AND run_id IN (SELECT id FROM collection_runs WHERE state IN {OPEN_RUNS})",
        (work["id"],),
    )
    if work["last_error_code"]:
        store.execute(
            "UPDATE notifications SET resolved_at=?,state=CASE WHEN state IN ('pending','retry_wait','blocked') THEN 'canceled' ELSE state END WHERE incident_code=? AND account_id=? AND resolved_at IS NULL",
            (now(), work["last_error_code"], work["account_id"]),
        )


def skip(store, run_id, work_id):
    with store.tx():
        run = store.run(run_id)
        require(run["state"] in ("running", "blocked", "retry_wait"), "INVALID_REQUEST", "已结束批次不能改写")
        item = store.one("SELECT * FROM collection_items WHERE run_id=? AND work_id=?", (run_id, work_id))
        if item and item["result"] == "skipped":
            return {"run_id": run_id, "work_id": work_id, "skipped": True}
        require(item and item["result"] in ("blocked", "pending"), "INVALID_REQUEST", "该作品无待处置结果")
        work = store.work(work_id)
        require(
            work["processing_state"] in ("blocked", "submit_unknown", "pending", "failed"),
            "WORK_RUNNING",
            "正在执行的作品不能直接跳过",
        )
        store.execute(
            "UPDATE collection_items SET result='skipped',finished_at=?,requires_notification=0 WHERE run_id=? AND work_id=?",
            (now(), run_id, work_id),
        )
    store.finish_runs()
    return {"run_id": run_id, "work_id": work_id, "skipped": True}
