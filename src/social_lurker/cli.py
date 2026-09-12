"""Versioned JSON command boundary. Provider payloads and credentials never escape it."""

import argparse
import sqlite3
import sys

from .config import Instance, message_limit
from .delivery import Delivery
from .errors import LurkerError, require
from .http import Client
from .lifecycle import Lifecycle
from .monitor import Monitor
from .operations import bind, check, configure, export, instance_status, routine_plan, uninstall
from .releases import discover
from .sources import TikHub
from .store import Store
from .util import canonical, parse_json, semver


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LurkerError("COMMAND_INVALID", "命令参数无效，请查看帮助")


def validate_input(command, p):
    specs = {
        ("setup", "check"): (set(), set()),
        ("setup", "bind"): (set(), {"bot_id", "routine_id", "host", "sources", "evidence"}),
        ("config", "validate"): (set(), set()),
        ("config", "set"): (set(), {"timezone", "monitor", "rate_limit", "limits"}),
        ("routine", "plan"): (set(), set()),
        ("status",): (set(), set()),
        ("watch", "list"): (set(), set()),
        ("watch", "add"): (set(), {"source", "platform", "author_id", "channel_id"}),
        ("watch", "source"): ({"watch_id", "variant"}, set()),
        ("watch", "purge"): ({"watch_id"}, {"confirmed"}),
        ("watch", "test-latest"): ({"watch_id"}, set()),
        ("poll",): (set(), {"automatic", "watch_id"}),
        ("metadata", "retry"): ({"update_id"}, set()),
        ("dispatch", "next"): (set(), {"foreground_test", "automatic", "test_images", "update_id"}),
        ("dispatch", "skip-queued"): ({"update_ids"}, set()),
        ("dispatch", "list"): (set(), {"state"}),
        ("api", "verify-and-resume"): ({"hold_id", "params"}, set()),
        ("export", "watches"): ({"path"}, set()),
        ("export", "updates"): ({"path"}, {"watch_id", "since", "until"}),
        ("upgrade", "check"): (set(), {"quiet"}),
        ("upgrade", "apply"): (set(), {"confirmed"}),
        ("maintenance", "resume"): (set(), set()),
        ("star", "invite"): ({"event"}, set()),
        ("star", "apply"): ({"account"}, {"confirmed"}),
    }
    for action in ("pause", "resume", "remove"):
        specs[("watch", action)] = ({"watch_id"}, set())
    for action in ("uninstall", "purge-instance"):
        specs[(action,)] = (
            set(),
            {"confirmed", "host_detached", "backup_path", "discard_backup", "abandon_unresolved"},
        )
    if command in {("dispatch", "report"), ("dispatch", "resolve")}:
        from .delivery import validate_receipt

        validate_receipt(p)
        return
    require(command in specs, "COMMAND_INVALID")
    required, optional = specs[command]
    require(required <= set(p) <= required | optional, "INPUT_INVALID")
    for key in (
        "automatic",
        "foreground_test",
        "test_images",
        "confirmed",
        "host_detached",
        "discard_backup",
        "abandon_unresolved",
        "quiet",
    ):
        if key in p:
            require(type(p[key]) is bool, "INPUT_INVALID")
    for key in ("watch_id", "update_id", "hold_id", "bot_id", "routine_id"):
        if key in p:
            require(isinstance(p[key], str) and 0 < len(p[key]) <= 512, "INPUT_INVALID")
    for key in ("since", "until"):
        if key in p:
            require(type(p[key]) is int and 0 <= p[key] < 4102444800, "INPUT_INVALID")
    if "since" in p and "until" in p:
        require(p["since"] <= p["until"], "INPUT_INVALID")


def public_watch(row):
    row = dict(row)
    row["scan_pending"] = row.pop("scan_state_json", None) is not None
    return row


def execute(instance, command, data):
    require(
        isinstance(data, dict) and type(data.get("protocol")) is int and data.get("protocol") == 1,
        "PROTOCOL_REQUIRED",
    )
    p = {k: v for k, v in data.items() if k != "protocol"}
    validate_input(command, p)
    store = Store(instance)
    client = Client(instance)
    source = TikHub(client)
    monitor = Monitor(instance, source)
    if command == ("setup", "check"):
        return check(instance)
    if command == ("setup", "bind"):
        return bind(instance, p)
    if command == ("config", "validate"):
        instance.gate("read")
        instance.credentials()
        return {"valid": True}
    if command == ("config", "set"):
        return configure(instance, p)
    if command == ("routine", "plan"):
        return routine_plan(instance)
    if command == ("status",):
        return instance_status(instance)
    if command == ("watch", "list"):
        return store.list()
    if command == ("watch", "add"):
        author = source.resolve_author(
            p.get("source"),
            platform=p.get("platform"),
            author_id=p.get("author_id"),
            channel_id=p.get("channel_id"),
        )
        return public_watch(store.add(author))
    if command[:1] == ("watch",) and command[1:] in {("pause",), ("resume",), ("remove",)}:
        return public_watch(
            store.transition(
                p["watch_id"], {"pause": "paused", "resume": "active", "remove": "removed"}[command[1]]
            )
        )
    if command == ("watch", "source"):
        return public_watch(store.change_variant(p["watch_id"], p["variant"]))
    if command == ("watch", "purge"):
        return store.purge(p["watch_id"], p.get("confirmed", False))
    if command == ("watch", "test-latest"):
        message_limit(instance.gate("read"))
        row = monitor.test_latest(p["watch_id"])
        if row["state"] == "blocked":
            monitor.metadata(row["id"])
        with instance.transaction("read") as (db, _):
            result = db.execute("SELECT id,state,reason FROM updates WHERE id=?", (row["id"],)).fetchone()
        return dict(result) if result else {"state": "not_eligible"}
    if command == ("poll",):
        return monitor.poll(automatic=p.get("automatic", False), watch_id=p.get("watch_id"))
    if command == ("metadata", "retry"):
        return monitor.metadata(p["update_id"])
    if command == ("dispatch", "next"):
        return Delivery(instance).next(
            foreground_test=p.get("foreground_test", False),
            automatic=p.get("automatic", False),
            test_images=p.get("test_images", False),
            update_id=p.get("update_id"),
        )
    if command in {("dispatch", "report"), ("dispatch", "resolve")}:
        return Delivery(instance).report(p)
    if command == ("dispatch", "skip-queued"):
        return Delivery(instance).skip(p["update_ids"])
    if command == ("dispatch", "list"):
        require(
            p.get("state", "unknown")
            in {"blocked", "queued", "sending", "sent", "unknown", "cancelled", "ignored"},
            "INPUT_INVALID",
        )
        with instance.transaction("read") as (db, _):
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,watch_id,platform,work_id,author_name,published_at,title,source_url,state,error_code,attempt_id,payload_hash,provider_message_id,send_started_at FROM updates WHERE state=? ORDER BY first_seen_at,id",
                    (p.get("state", "unknown"),),
                )
            ]
    if command == ("api", "verify-and-resume"):
        return client.verify(p["hold_id"], p["params"])
    if command[:1] == ("export",) and command[1:] in {("watches",), ("updates",)}:
        return export(
            instance,
            command[1],
            p["path"],
            watch_id=p.get("watch_id"),
            since=p.get("since"),
            until=p.get("until"),
        )
    if command == ("upgrade", "check"):
        try:
            settings = instance.gate("read")
            d = discover()
            newer = semver(d["manifest"]["version"]) > semver(settings["app_version"])
            return {"available": newer, "current": settings["app_version"], "release": d if newer else None}
        except LurkerError:
            if p.get("quiet"):
                return None
            raise
    if command == ("upgrade", "apply"):
        require(p.get("confirmed") is True, "UPGRADE_AUTHORIZATION_REQUIRED")
        # Never trust a descriptor supplied in chat. Resolve the fixed official source again.
        if instance.plan():
            return Lifecycle(instance).resume()
        return Lifecycle(instance).begin(discover(), confirmed=True)
    if command == ("maintenance", "resume"):
        return Lifecycle(instance).resume()
    if command == ("star", "invite"):
        from .star import invite

        return invite(p["event"])
    if command == ("star", "apply"):
        from .star import apply

        return apply(p["account"], p.get("confirmed", False))
    if command in {("uninstall",), ("purge-instance",)}:
        return uninstall(instance, purge=command == ("purge-instance",), **p)
    raise LurkerError("COMMAND_INVALID")


def main(argv=None):
    result = None
    instance = None
    code = 0
    try:
        parser = Parser(
            description="盯梢者轻量 R1：显式实例 + protocol=1 JSON。完整命令见 skills/social-lurker。"
        )
        parser.add_argument("--instance", required=True)
        parser.add_argument(
            "--json", dest="json_input", help="JSON 参数；省略时从标准输入读取，密钥不得作为参数传入"
        )
        parser.add_argument("command", nargs="+")
        args = parser.parse_args(argv)
        raw = args.json_input
        if raw is None:
            require(not sys.stdin.isatty(), "PROTOCOL_REQUIRED")
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        data = parse_json(raw)
        instance = Instance(args.instance)
        result = {"protocol": 1, "ok": True, "result": execute(instance, tuple(args.command), data)}
    except LurkerError as error:
        result = {"protocol": 1, "ok": False, "error": error.public()}
        code = 2
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        result = {
            "protocol": 1,
            "ok": False,
            "error": LurkerError("INPUT_OR_STATE_INVALID", "参数或持久状态无效，未透传原始内容").public(),
        }
        code = 2
    except (OSError, sqlite3.Error):
        result = {
            "protocol": 1,
            "ok": False,
            "error": LurkerError("LOCAL_OPERATION_FAILED", "本地读写失败，请核对实例状态").public(),
        }
        code = 2
    if result is not None:
        if (
            instance is not None
            and not result.get("ok")
            and "args" in locals()
            and args.command[0] not in {"status", "config", "star", "upgrade", "setup", "export"}
        ):
            from .logs import record

            record(instance, result["error"]["code"])
        print(canonical(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
