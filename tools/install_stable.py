#!/usr/bin/env python3
"""Install highest verified stable release; no routine, Git delivery or Star side effects."""

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from social_lurker.config import Instance  # noqa: E402
from social_lurker.errors import LurkerError, require  # noqa: E402
from social_lurker.store import Store  # noqa: E402
from social_lurker.upgrade import discover, prepare_package  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/workspace/social-lurker"))
    parser.add_argument("--instance")
    parser.add_argument("--binding-confirmed", action="store_true", required=True)
    parser.add_argument("--platform-bot-id")
    args = parser.parse_args()
    root = args.root.resolve()
    descriptor = discover()
    package = prepare_package(root, descriptor)
    launcher = root / "runtime/launcher.py"
    candidate = package / "launcher.py"
    if launcher.exists():
        require(
            launcher.read_bytes() == candidate.read_bytes(),
            "LAUNCHER_CONFLICT",
            "已有共享 launcher 不同，需明确跨实例范围",
        )
    else:
        shutil.copyfile(candidate, launcher)
    instance = Instance(root, args.instance or str(uuid.uuid4()))
    if instance.settings_path.exists():
        settings = instance.load(platform_bot_id=args.platform_bot_id)
        require(
            settings["runtime_version"] == descriptor["manifest"]["version"],
            "INSTANCE_EXISTS",
            "已有实例请使用 upgrade，不能安装覆盖",
        )
    else:
        instance.initialize(
            binding_confirmed=True,
            platform_bot_id=args.platform_bot_id,
            runtime_version=descriptor["manifest"]["version"],
        )
        Store(instance.db_path).close()
    print(
        json.dumps(
            {
                "instance_id": instance.id,
                "version": descriptor["manifest"]["version"],
                "launcher": str(launcher),
                "next": "configure local credentials; doctor; native Grok Bot binding, notification and routine acceptance",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except LurkerError as error:
        print(json.dumps({"status": "blocked", "error": error.public()}, ensure_ascii=False))
        raise SystemExit(1)
