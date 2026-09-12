#!/usr/bin/env python3
"""Stable, stdlib-only protocol-1 dispatcher. The instance owns its version binding."""

import json
import re
import sys
import uuid
from pathlib import Path


def main():
    request_id = None
    try:
        root = Path(__file__).resolve().parent.parent
        args = sys.argv[1:]
        index = args.index("--instance")
        instance = args[index + 1]
        if str(uuid.UUID(instance)) != instance:
            raise ValueError()
        raw = sys.stdin.buffer.read()
        request = json.loads(raw)
        request_id = request.get("request_id")
        if request.get("protocol_version") != 1:
            raise ValueError()
        settings_path = root / "bots" / instance / "settings.json"
        if settings_path.is_symlink() or not settings_path.resolve().is_relative_to(root):
            raise ValueError()
        settings = json.loads(settings_path.read_text())
        version = settings["runtime_version"]
        if settings["instance_id"] != instance or not re.fullmatch(r"\d+\.\d+\.\d+(?:-dev)?", version):
            raise ValueError()
        package = root / "runtime/releases" / version
        python = package / ".venv/bin/python"
        if package.is_symlink() or not python.is_file():
            raise ValueError()
        # exec with an anonymous input pipe: no request/credential file and no shell interpolation.
        import subprocess

        process = subprocess.run(
            [str(python), "-m", "social_lurker", "--root", str(root), *args], cwd=package / "app", input=raw
        )
        return process.returncode
    except (ValueError, KeyError, IndexError, OSError, TypeError):
        print(
            json.dumps(
                {
                    "protocol_version": 1,
                    "request_id": request_id,
                    "status": "blocked",
                    "result": None,
                    "error": {"code": "INSTANCE_BINDING_REQUIRED", "message": "请核对实例、配置和已安装版本"},
                    "pending_notification_ids": [],
                    "pending_proofread_work_ids": [],
                    "next_action_at": None,
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
