#!/usr/bin/env python3
"""Explicit development install. Never presents an uncommitted checkout as a stable release."""

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from social_lurker.config import Instance  # noqa: E402
from social_lurker.errors import require  # noqa: E402
from social_lurker.store import Store  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--binding-confirmed", action="store_true", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    package = root / "runtime/releases/0.1.0-dev"
    require(
        not package.exists(),
        "DEV_INSTALL_EXISTS",
        "开发包已存在，请使用新临时根目录，避免覆盖其他 Bot 运行代码",
    )
    package.mkdir(parents=True)
    try:
        shutil.copytree(
            REPO / "src/social_lurker",
            package / "app/social_lurker",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copytree(REPO / "vendor", package / "vendor")
        shutil.copytree(REPO / "skills", package / "skills")
        subprocess.run([sys.executable, "-m", "venv", str(package / ".venv")], check=True, timeout=120)
        subprocess.run(
            [
                str(package / ".venv/bin/python"),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "--only-binary=:all:",
                "-r",
                str(REPO / "requirements-runtime.txt"),
            ],
            check=True,
            timeout=300,
        )
        launcher = root / "runtime/launcher.py"
        if launcher.exists():
            require(
                launcher.read_bytes() == (REPO / "tools/launcher.py").read_bytes(),
                "LAUNCHER_CONFLICT",
                "共享 launcher 已有不同内容",
            )
        else:
            shutil.copyfile(REPO / "tools/launcher.py", launcher)
        instance = Instance(root, args.instance or str(uuid.uuid4()))
        instance.initialize(binding_confirmed=True, runtime_version="0.1.0-dev")
        Store(instance.db_path).close()
        (package / "manifest.json").write_text(
            json.dumps(
                {
                    "project": "social-lurker",
                    "version": "0.1.0-dev",
                    "channel": "development",
                    "not_a_stable_release": True,
                }
            )
            + "\n"
        )
        print(
            json.dumps(
                {
                    "instance_id": instance.id,
                    "launcher": str(launcher),
                    "data_directory": str(instance.path),
                    "runtime_version": "0.1.0-dev",
                    "grok_acceptance_pending": True,
                },
                ensure_ascii=False,
            )
        )
    except BaseException:
        shutil.rmtree(package)
        raise


if __name__ == "__main__":
    main()
