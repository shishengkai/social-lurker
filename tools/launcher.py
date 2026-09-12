#!/usr/bin/env python3
"""Stable, stdlib-only instance launcher. Select a handler before opening any SQLite."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def safe(path):
    p = Path(path).absolute()
    if ".." in p.parts or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError("unsafe path")
    return p


def load(path):
    raw = safe(path).read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("oversize state")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def main():
    try:
        if sys.version_info < (3, 12):
            raise ValueError("Python 3.12 required")
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--instance", required=True)
        args, remaining = parser.parse_known_args()
        if not Path(args.instance).is_absolute():
            raise ValueError("explicit absolute instance required")
        root = safe(args.instance)
        if safe(__file__).parent != root:
            raise ValueError("entry bound to another instance")
        raw = None
        if "--json" not in remaining:
            if sys.stdin.isatty():
                raise ValueError("protocol input required")
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        for _ in range(4):
            settings = load(root / "settings.json")
            version = settings["app_version"]
            plan_path = safe(root / "maintenance.json")
            if plan_path.exists():
                plan = load(plan_path)
                if plan.get("protocol") != 1 or plan.get("instance_id") != settings["instance_id"]:
                    raise ValueError("invalid maintenance")
                stage = plan["stage"]
                if stage not in {
                    "prepared",
                    "frozen",
                    "candidate_ready",
                    "switching",
                    "committed",
                    "rolled_back",
                    "done",
                }:
                    raise ValueError("unknown stage")
                if type(plan.get("business_writes_open")) is not bool:
                    raise ValueError("invalid gate")
                version = (
                    plan["target_version"]
                    if stage == "committed" or (stage == "done" and plan.get("result") == "upgraded")
                    else plan["source_version"]
                )
            if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
                raise ValueError("invalid version")
            folder = safe(root / "app" / version)
            # Hash every selected executable against its pinned installation manifest.
            import hashlib

            manifest = load(folder / "manifest.json")
            if (
                manifest.get("version") != version
                or manifest.get("product") != "social-lurker-lightweight"
                or manifest.get("authority") != "github.com/shishengkai/social-lurker"
            ):
                raise ValueError("invalid manifest")
            expected = set(manifest["files"]) | {"manifest.json"}
            actual = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
            if actual != expected or any(p.is_symlink() for p in folder.rglob("*")):
                raise ValueError("unexpected package files")
            if not {
                "social_lurker/cli.py",
                "social_lurker/__init__.py",
                "social_lurker/schema.sql",
                "run.py",
            } <= set(manifest["files"]):
                raise ValueError("incomplete package")
            for name, hashed in manifest["files"].items():
                target = safe(folder / name)
                if (
                    not target.is_relative_to(folder)
                    or hashlib.sha256(target.read_bytes()).hexdigest() != hashed
                ):
                    raise ValueError("package changed")
            boot = "import sys;sys.path.insert(0,sys.argv.pop(1));from social_lurker.cli import main;raise SystemExit(main())"
            process = subprocess.run(
                [sys.executable, "-I", "-B", "-c", boot, str(folder), *sys.argv[1:]],
                input=raw,
                capture_output=True,
            )
            try:
                envelope = json.loads(process.stdout)
            except (ValueError, UnicodeError):
                raise ValueError("handler failed") from None
            if (
                envelope.get("ok")
                and isinstance(envelope.get("result"), dict)
                and envelope["result"].get("reenter")
            ):
                # Reenter only the persisted recovery operation, never repeat the original business action.
                sys.argv = [
                    sys.argv[0],
                    "--instance",
                    str(root),
                    "--json",
                    '{"protocol":1}',
                    "maintenance",
                    "resume",
                ]
                raw = None
                continue
            sys.stdout.buffer.write(process.stdout)
            return process.returncode
        raise ValueError("recovery loop")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(
            json.dumps(
                {
                    "protocol": 1,
                    "ok": False,
                    "error": {
                        "code": "ENTRY_STATE_INVALID",
                        "message": "稳定入口无法核验实例或维护状态；没有启动业务写入",
                        "retryable": False,
                        "next_action": "核对本实例 settings、maintenance 与固定版本文件",
                    },
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
