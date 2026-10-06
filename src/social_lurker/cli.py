import argparse
import getpass
import os
import signal
import sqlite3
import sys
import warnings
from contextlib import ExitStack, nullcontext
from pathlib import Path

from . import __version__, upgrade
from .adapters.base import PLATFORMS, identity
from .adapters.douyin import Douyin
from .adapters.links import source_link
from .adapters.wechat import Wechat
from .config import DEFAULT_CREDENTIALS, DEFAULT_DATA, DEFAULT_INSTALL, Config, FileSecrets
from .db import Database
from .errors import LurkerError, require
from .locks import lock
from .output import Output, summary
from .package import VERSION
from .profiles import FileRegistry
from .service import Service
from .transport import Transport


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LurkerError("INPUT_INVALID")


def parser():
    p = Parser(
        prog="social-lurker", description=f"盯梢者 {__version__}：作品元信息 CLI。全局选项放在命令前。"
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--profile", help="业务命令必需，稳定编号，例如 p0001")
    p.add_argument("--format", choices=("jsonl", "json"), default="jsonl")
    p.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    p.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL)
    p.add_argument("--credentials-file", type=Path, default=DEFAULT_CREDENTIALS)
    p.add_argument("--rps", type=float, default=1.0)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--request-retries", type=int, default=0)
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    sub.add_parser("add", help="只登记首页基线").add_argument("link")
    sub.add_parser("list", help="关注作者").add_argument("--all", action="store_true")
    unfollow = sub.add_parser("unfollow", help="停止关注，保留历史")
    unfollow.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    unfollow.add_argument("--author-id", required=True)
    sub.add_parser("check", help="从首页进行有限增量查询")
    profiles = sub.add_parser("profile", help="自动编号与显示标签")
    actions = profiles.add_subparsers(dest="action", required=True, parser_class=Parser)
    actions.add_parser("create").add_argument("--label", required=True)
    actions.add_parser("list")
    config = sub.add_parser("config", help="配置共享凭据，不输出密钥")
    actions = config.add_subparsers(dest="action", required=True, parser_class=Parser)
    actions.add_parser("set-key").add_argument("--stdin", action="store_true", help="从标准输入读取单行 key")
    actions.add_parser("status")
    actions.add_parser("check", help="免费认证检查，不查询作品")
    up = sub.add_parser("upgrade", help="显式检查或应用正式版本")
    actions = up.add_subparsers(dest="action", required=True, parser_class=Parser)
    actions.add_parser("check")
    actions.add_parser("apply").add_argument("--version", required=True)
    return p


def requested_format(argv):
    # An otherwise invalid command still uses the explicitly recognizable output format.
    result = "jsonl"
    for index, value in enumerate(argv):
        if value == "--format" and index + 1 < len(argv) and argv[index + 1] in {"json", "jsonl"}:
            result = argv[index + 1]
        elif value in {"--format=json", "--format=jsonl"}:
            result = value.split("=", 1)[1]
    return result


def _run(
    argv=None,
    *,
    registry=None,
    secrets=None,
    adapters=None,
    transport_factory=Transport,
    stdout=None,
    install_locked=False,
):
    argv = list(sys.argv[1:] if argv is None else argv)
    output = Output(fmt=requested_format(argv), stream=stdout)
    stats = summary()
    status, exit_code, scan_complete = "ok", 0, None
    old_handlers = {}

    def interrupted(number, frame):
        raise LurkerError("INTERRUPTED", exit_code=130 if number == signal.SIGINT else 143)

    try:
        args = parser().parse_args(argv)
        command = args.command + ("." + args.action if hasattr(args, "action") else "")
        output.common.update(
            command=command,
            profile_id=args.profile if args.command in {"add", "list", "unfollow", "check"} else None,
        )
        if args.command == "check":
            scan_complete = False
        if args.command in {"profile", "config", "upgrade"}:
            require(args.profile is None, "INPUT_INVALID")
        else:
            require(args.profile is not None, "INPUT_INVALID")
        config = Config(args.rps, args.timeout, args.request_retries)
        config.validate()
        if args.command == "upgrade" and args.action == "apply":
            require(VERSION.fullmatch(args.version), "INPUT_INVALID")
        for number in (signal.SIGINT, signal.SIGTERM):
            old_handlers[number] = signal.signal(number, interrupted)
        root = args.install_root.expanduser().resolve()
        data_root = args.data_root.expanduser().resolve()
        registry = registry or FileRegistry(data_root)
        secrets = secrets or FileSecrets(args.credentials_file)
        exclusive = args.command == "upgrade" and args.action == "apply"
        if not install_locked and (root / "upgrade-state.json").exists():
            with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
                upgrade.recover(root)
        guard = (
            nullcontext()
            if install_locked
            else lock(root / "install.lock", shared=not exclusive, code="MAINTENANCE_BUSY")
        )
        with guard, ExitStack() as stack:
            # Recheck under the lock; no business writes while an unfinished plan exists.
            require(not (root / "upgrade-state.json").exists(), "MAINTENANCE_BUSY")
            if (root / "current.json").exists():
                pointer, _ = upgrade.current(root)
                require(pointer["version"] == __version__, "MAINTENANCE_BUSY")
                require(Path(pointer["data_root"]).resolve() == data_root, "CONFIG_INVALID")
            if args.command == "upgrade":
                payload = upgrade.check(root) if args.action == "check" else upgrade.apply(root, args.version)
            elif args.command == "profile":
                if args.action == "create":
                    profile = registry.create(args.label)
                    payload = {"profile_id": profile.profile_id, "label": profile.label}
                else:
                    payload = {
                        "profiles": [{"profile_id": p.profile_id, "label": p.label} for p in registry.list()]
                    }
            elif args.command == "config":
                if args.action == "set-key":
                    if args.stdin:
                        key = sys.stdin.readline(4098).rstrip("\r\n")
                    else:
                        require(sys.stdin.isatty(), "INPUT_INVALID")
                        try:
                            with warnings.catch_warnings():
                                warnings.simplefilter("error", getpass.GetPassWarning)
                                key = getpass.getpass("TikHub API key（隐藏输入）：", stream=sys.stderr)
                        except (getpass.GetPassWarning, EOFError):
                            raise LurkerError("CONFIG_INVALID") from None
                    secrets.set_key(key)
                payload = secrets.status()
                if args.action == "check":
                    transport = transport_factory(secrets, stats, config, before_request=output.ensure_open)
                    payload = {**transport.check_credentials(), "credential_source": payload["source"]}
            else:
                profile = registry.get(args.profile)
                if args.command != "list":
                    stack.enter_context(lock(profile.lock_path))
                db = Database(profile.db_path, profile.profile_id)
                stack.callback(db.close)
                if adapters is None:
                    transport = transport_factory(secrets, stats, config, before_request=output.ensure_open)
                    adapters = {"douyin": Douyin(transport), "wechat_channels": Wechat(transport)}
                service = Service(db, adapters, output, stats)
                if args.command == "add":
                    platform, link = source_link(args.link)
                    secrets.get_key()
                    payload = service.add(link, adapters[platform])
                elif args.command == "list":
                    payload = service.list(args.all)
                elif args.command == "unfollow":
                    identity(args.author_id)
                    payload = db.unfollow(args.platform, args.author_id)
                else:
                    # Empty active snapshots do not require a key or issue a request.
                    if db.authors():
                        secrets.get_key()
                    status, exit_code = service.check()
                    scan_complete = status == "ok"
                    payload = None
            if payload is not None:
                output.record("result", payload=payload)
    except LurkerError as error:
        if error.code == "OUTPUT_CLOSED":
            return 141
        status, exit_code = "error", error.exit_code
        # A signal can arrive outside Service's error handler; preserve snapshot accounting.
        accounted = stats["authors_succeeded"] + stats["authors_failed"] + stats["authors_unchecked"]
        stats["authors_failed"] += max(0, stats["authors_total"] - accounted)
        try:
            output.record("error", error=error.public())
        except LurkerError:
            return 141
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, IndexError):
        status, exit_code = "error", 1
        accounted = stats["authors_succeeded"] + stats["authors_failed"] + stats["authors_unchecked"]
        stats["authors_failed"] += max(0, stats["authors_total"] - accounted)
        code = (
            "UPGRADE_FAILED" if output.common["command"] in {"upgrade.apply", "upgrade.check"} else "DB_ERROR"
        )
        try:
            output.record("error", error=LurkerError(code).public())
        except LurkerError:
            return 141
    except SystemExit as error:
        return error.code  # --help/--version explicitly allow human output.
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
    try:
        output.complete(status, stats, scan_complete)
    except LurkerError:
        return 141
    finally:
        output.close()
    return exit_code


def main(argv=None, **dependencies):
    code = _run(argv, **dependencies)
    if code == 141 and dependencies.get("stdout") is None:
        # CPython must not flush a retained buffer to a dead pipe and replace exit 141 with 120.
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except (OSError, ValueError, AttributeError):
            pass
    return code
