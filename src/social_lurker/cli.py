import argparse
import contextlib
import copy
import json
import sys
import uuid

from . import __version__
from .config import Instance, doctor, validate
from .control import Journal
from .engine import Engine
from .errors import LurkerError, require
from .notifications import Notifications
from .operations import collection_plan, confirm_collection, prepare_collection, retry, skip
from .proofread import Proofreader
from .store import Store
from .util import file_lock, now, write_json


def merge_settings(original, changes):
    result = copy.deepcopy(original)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_settings(result[key], value)
        else:
            result[key] = value
    return result


def dispatch(instance, store, settings, command, request):
    payload = request["payload"]
    action = payload.get("action", "get")
    journal = Journal(instance)
    engine = Engine(instance, store, settings) if settings else None

    def mutate(summary, operation, prepare=lambda: {}):
        return journal.perform(request, command + ":" + action, summary, operation, prepare)

    if command == "init":
        return {
            "instance_id": instance.id,
            "data_directory": str(instance.path),
            "version": __version__,
            "next": "config credentials, doctor, then Grok Bot binding/delivery/routine acceptance",
        }
    if command == "doctor":
        result = doctor(instance, settings)
        result["checks"]["database"] = (
            store.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and not store.execute("PRAGMA foreign_key_check").fetchall()
        )
        result["local_ready"] = all(v for k, v in result["checks"].items() if k != "delivery_verified")
        return result
    if command == "setup":
        from .setup import status

        require(action == "status", "INVALID_REQUEST", "setup 支持 action:status")
        return status(instance, settings, store)
    if command == "status":
        return store.summary()
    if command == "config":
        if action == "get":
            return {
                "settings": settings,
                "credentials_present": {k: bool(v) for k, v in instance.credentials().items()},
            }
        if action == "credentials":
            values = payload.get("values", {})

            def set_credentials(_):
                instance.set_credentials(values)
                return {"updated_fields": sorted(values), "values_stored": "current instance .env"}

            return mutate({"credential_fields": sorted(values)}, set_credentials)
        require(
            action == "set" and isinstance(payload.get("settings"), dict),
            "INVALID_REQUEST",
            "不支持的配置操作",
        )
        updated = merge_settings(settings, payload["settings"])
        require(
            updated["instance_id"] == settings["instance_id"]
            and updated["runtime_version"] == settings["runtime_version"]
            and updated["platform_bot_id"] == settings["platform_bot_id"],
            "CONFIG_INVALID",
            "配置不能改变实例身份或绕过升级绑定版本",
        )
        validate(updated, instance.id)
        if updated["delivery"]["verified"] and not settings["delivery"]["verified"]:
            require(
                payload.get("native_delivery_test_passed") is True,
                "DELIVERY_UNVERIFIED",
                "须在真实 Grok Bot 完成交付测试后启用",
            )

        def save(_):
            write_json(instance.settings_path, updated)
            return {
                "updated": True,
                "routine_sync_required": updated["check_interval_minutes"]
                != updated["routine"]["verified_interval_minutes"],
            }

        return mutate({"settings": payload["settings"]}, save)
    if command == "accounts":
        if action == "list":
            return {"accounts": store.all("SELECT * FROM accounts ORDER BY id")}
        if action == "add":
            require(
                payload.get("accept_service_costs") is True,
                "COST_ACK_REQUIRED",
                "需告知默认 30 分钟检查及 TikHub/Whisper 可能计费",
            )

            def add(_):
                metadata = engine.social.resolve(payload["source"])
                return store.add_account(metadata, request["request_id"])

            result = mutate({"source": payload.get("source")}, add)
            # Add is immediate: bounded initial collection proceeds even if history question is unanswered.
            if result.get("created"):
                engine.tick(schedule=False)
            return result
        require(action in ("stop", "resume", "delete"), "INVALID_REQUEST", "不支持的账号操作")
        aid = payload["account_id"]

        def control(intent):
            if not store.one("SELECT id FROM accounts WHERE id=?", (aid,)) and action == "delete":
                return {"account_id": aid, "state": "deleted"}
            account = store.control(aid, action, intent["epoch"])
            return {
                "account_id": aid,
                "state": account["tracking_state"],
                "watch_epoch": account["watch_epoch"],
            }

        return mutate({"account_id": aid}, control, lambda: {"epoch": store.account(aid)["watch_epoch"]})
    if command == "collect":
        if action == "prepare":
            result = mutate(
                {k: payload.get(k) for k in ("account_id", "scope", "amount", "unit", "count", "mode")},
                lambda _: prepare_collection(
                    store, payload["account_id"], payload, request["request_id"], settings["timezone"]
                ),
            )
            engine.enumerate_run(result["run_id"])
            return collection_plan(store, result["run_id"])
        if action == "status":
            return collection_plan(store, payload["run_id"])
        require(action == "confirm", "INVALID_REQUEST", "不支持的历史操作")
        return mutate(
            {"run_id": payload["run_id"], "count": payload.get("acknowledged_count")},
            lambda _: confirm_collection(store, payload["run_id"], payload),
        )
    if command == "tick":
        return engine.tick()
    if command == "retry":

        def prepare_retry():
            if payload.get("work_id"):
                return {"cycle": store.work(payload["work_id"])["execution_cycle"]}
            run = store.run(payload["run_id"])
            return {"run_state": run["state"]}

        def perform_retry(intent):
            if (
                payload.get("work_id")
                and store.work(payload["work_id"])["execution_cycle"] != intent["cycle"]
            ):
                return {"work_id": payload["work_id"], "recovered": True}
            if (
                payload.get("run_id")
                and intent.get("run_state") in ("blocked", "retry_wait")
                and store.run(payload["run_id"])["state"] != intent["run_state"]
            ):
                return {"run_id": payload["run_id"], "recovered": True}
            return retry(store, payload, request["request_id"])

        return mutate(
            {
                k: payload.get(k)
                for k in ("work_id", "run_id", "work_ids", "external_job_id", "accept_duplicate_charge_risk")
            },
            perform_retry,
            prepare_retry,
        )
    if command == "skip":
        return mutate(
            {"run_id": payload["run_id"], "work_id": payload["work_id"]},
            lambda _: skip(store, payload["run_id"], payload["work_id"]),
        )
    if command == "proofread":
        proof = Proofreader(store, settings)
        if action == "next":
            return {"segment": proof.next(payload.get("work_id"))}
        if action == "submit":
            return proof.submit(payload)
        if action == "status":
            return {
                "works": store.all(
                    "SELECT id,processing_state,raw_text_hash,last_error_code FROM works WHERE processing_state IN ('pending_proofread','proofreading')"
                )
            }
    if command == "notifications":
        notifications = Notifications(store, settings)
        if action == "list":
            return {"notifications": notifications.list()}
        if action == "claim":
            return notifications.claim(payload["notification_id"])
        if action == "render":
            return notifications.render(payload["notification_id"], payload["dispatch_token"])
        if action == "ack":
            return notifications.ack(
                payload["notification_id"], payload["dispatch_token"], payload.get("evidence")
            )
        if action == "resolve":
            return notifications.resolve(payload["notification_id"], payload["resolution"], payload)
    if command == "upgrade":
        from .upgrade import apply_upgrade, discover, is_newer, recover_upgrade

        if action == "recover":
            return recover_upgrade(instance, store)
        if action in ("check", "apply"):
            if action == "check":
                try:
                    descriptor = discover()
                except LurkerError:
                    if payload.get("automatic") is True:
                        return {"update": None}
                    raise
                latest = descriptor["manifest"]
                return {
                    "update": {
                        "version": latest["version"],
                        "summary": latest["summary"],
                        "release_commit": descriptor["release_commit"],
                    }
                    if is_newer(latest["version"], settings["runtime_version"])
                    else None
                }
            require(
                payload.get("authorized") is True, "UPGRADE_AUTHORIZATION_REQUIRED", "需要用户已明确要求升级"
            )
            plan_path = instance.maintenance / "upgrade-plan.json"
            if plan_path.exists():
                descriptor = json.loads(plan_path.read_text())
            else:
                descriptor = discover()
                write_json(plan_path, descriptor)
            result = apply_upgrade(instance, store, settings, descriptor)
            if result.get("upgraded"):
                plan_path.unlink(missing_ok=True)
            return result
    if command == "uninstall":

        def stop_all(_):
            for account in store.all("SELECT id FROM accounts WHERE tracking_state='active'"):
                store.control(account["id"], "stop")
            return {
                "data_retained": True,
                "next": "finish existing runs; remove only this Bot routine and binding using verified native tools",
            }

        return mutate({"instance_id": instance.id}, stop_all)
    if command == "star":
        from .star import apply, invite

        if action == "invite":
            return {"invitation": invite(payload.get("event"))}
        if action == "apply":
            return apply(payload.get("account"), payload.get("confirmed"))
    raise LurkerError("INVALID_REQUEST", "命令或 action 不受支持")


def envelope(request_id, *, result=None, error=None, store=None):
    pending, proof = [], []
    due = None
    if store:
        pending = [
            r["id"]
            for r in store.all(
                "SELECT id FROM notifications WHERE state IN ('pending','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY id",
                (now(),),
            )
        ]
        proof = [
            r["id"]
            for r in store.all(
                "SELECT id FROM works WHERE processing_state IN ('pending_proofread','proofreading') ORDER BY id"
            )
        ]
        row = store.one("""SELECT min(t) AS value FROM (SELECT next_attempt_at AS t FROM works UNION ALL
          SELECT next_check_at AS t FROM accounts WHERE tracking_state='active' UNION ALL
          SELECT next_attempt_at AS t FROM collection_runs) WHERE t IS NOT NULL""")
        due = row["value"] if row else None
    status = (
        "blocked"
        if error
        else ("pending" if pending or proof or (isinstance(result, dict) and result.get("pending")) else "ok")
    )
    return {
        "protocol_version": 1,
        "request_id": request_id,
        "status": status,
        "result": result,
        "error": error,
        "pending_notification_ids": pending,
        "pending_proofread_work_ids": proof,
        "next_action_at": due,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="盯梢者本地 JSON 入口")
    parser.add_argument("--root", default="/workspace/social-lurker")
    parser.add_argument("--instance")
    parser.add_argument(
        "command",
        choices=[
            "init",
            "doctor",
            "setup",
            "config",
            "accounts",
            "collect",
            "tick",
            "status",
            "retry",
            "skip",
            "proofread",
            "notifications",
            "upgrade",
            "uninstall",
            "star",
        ],
    )
    parser.add_argument("--request-stdin", action="store_true", required=True)
    args = parser.parse_args(argv)
    request_id, store, exit_code = None, None, 0
    try:
        request = json.load(sys.stdin)
        require(
            isinstance(request, dict)
            and request.get("protocol_version") == 1
            and isinstance(request.get("request_id"), str)
            and 0 < len(request["request_id"]) <= 200
            and isinstance(request.get("payload"), dict),
            "INVALID_REQUEST",
            "请求需 protocol_version=1、request_id 和 payload",
        )
        request_id = request["request_id"]
        payload = request["payload"]
        instance_id = args.instance
        if args.command == "init" and not instance_id:
            instance_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "social-lurker:" + request_id))
        instance = Instance(args.root, instance_id)
        from .setup import configure_tool_path

        configure_tool_path(instance.root)
        if args.command == "init":
            with file_lock(instance.maintenance / "instance.lock"):
                settings = instance.initialize(
                    binding_confirmed=payload.get("binding_confirmed"),
                    platform_bot_id=payload.get("platform_bot_id"),
                )
                store = Store(instance.db_path)
        else:
            require(instance.db_path.exists(), "INSTANCE_NOT_INITIALIZED", "该实例尚未初始化")
            store = Store(instance.db_path)
        control = args.command == "accounts" and payload.get("action") in ("stop", "delete")
        read_only = args.command in ("doctor", "status", "setup") or (
            args.command == "accounts" and payload.get("action") == "list"
        )
        if control:
            # Explicit UUID permits stopping even with broken ordinary settings. No new external work.
            settings = None
        else:
            settings = instance.load(platform_bot_id=payload.get("platform_bot_id"))
        lock = (
            contextlib.nullcontext()
            if control or read_only or args.command == "init"
            else file_lock(instance.maintenance / "instance.lock")
        )
        with lock:
            maintenance_path = instance.maintenance / "upgrade.json"
            if maintenance_path.exists():
                phase = json.loads(maintenance_path.read_text())["stage"]
                require(
                    phase == "draining" or read_only or control or args.command == "upgrade",
                    "MAINTENANCE",
                    "迁移未完成，业务写入暂时关闭",
                )
                require(
                    args.command not in ("init", "collect")
                    and not (args.command == "accounts" and payload.get("action") in ("add", "resume")),
                    "MAINTENANCE",
                    "升级维护期不能新建工作",
                )
            result = dispatch(instance, store, settings, args.command, request)
        response = envelope(request_id, result=result, store=store)
        if args.command != "star":
            from .logs import record

            record(
                instance.path / "logs",
                args.command,
                response["status"],
                settings.get("logging") if settings else None,
            )
    except (json.JSONDecodeError, UnicodeError):
        response, exit_code = (
            envelope(request_id, error={"code": "INVALID_REQUEST", "message": "JSON 无效"}),
            2,
        )
    except (KeyError, TypeError, ValueError):
        response, exit_code = (
            envelope(request_id, error={"code": "INVALID_REQUEST", "message": "请求字段缺失或类型无效"}),
            2,
        )
    except LurkerError as error:
        response = envelope(request_id, error=error.public(), store=store)
        if error.code == "INVALID_REQUEST":
            exit_code = 2
    except Exception as error:
        # Never print exception strings/tracebacks: URLs, credentials or content can be embedded in them.
        response, exit_code = (
            envelope(
                request_id,
                error={"code": "INTERNAL_ERROR", "message": "程序发生内部错误；资料与执行状态保留"},
            ),
            1,
        )
        print(json.dumps({"event": "internal_error", "type": type(error).__name__}), file=sys.stderr)
    finally:
        if store:
            store.close()
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return exit_code
