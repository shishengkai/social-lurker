#!/usr/bin/env python3
"""Deliver an explicitly pinned development commit through the installed recovery coordinator."""
# ruff: noqa: E402

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from build_release import build

from social_lurker.config import Instance
from social_lurker.errors import LurkerError, require
from social_lurker.releases import package_bytes, verify_directory
from social_lurker.util import digest, parse_json, safe_path

ORIGINS = {
    "https://github.com/shishengkai/social-lurker.git",
    "https://github.com/shishengkai/social-lurker",
    "git@github.com:shishengkai/social-lurker.git",
}


def git(source, *args):
    try:
        return subprocess.check_output(["git", *args], cwd=source, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise LurkerError("PREVIEW_SOURCE_INVALID") from None


def source_path(name):
    if name == "run.py":
        return "tools/launcher.py"
    return "src/" + name if name.startswith("social_lurker/") else name


def verify_source(source, commit):
    source = safe_path(source)
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit), "PREVIEW_COMMIT_REQUIRED")
    require(git(source, "remote", "get-url", "origin").decode().strip() in ORIGINS, "PREVIEW_SOURCE_INVALID")
    require(git(source, "rev-parse", "HEAD").decode().strip() == commit, "PREVIEW_COMMIT_MISMATCH")
    require(not git(source, "status", "--porcelain", "--untracked-files=all").strip(), "SOURCE_DIRTY")
    tracked = set(git(source, "ls-files", "-z").decode().split("\0"))
    for name, raw in package_bytes(source).items():
        path = source_path(name)
        require(path in tracked, "PREVIEW_UNTRACKED_PACKAGE_FILE")
        require(git(source, "show", f"{commit}:{path}") == raw, "PREVIEW_COMMIT_MISMATCH")
    match = re.search(
        r'__version__\s*=\s*["\x27]([0-9]+\.[0-9]+\.[0-9]+)["\x27]',
        (source / "src/social_lurker/__init__.py").read_text(),
    )
    require(match is not None, "PREVIEW_VERSION_INVALID")
    return match[1]


def upgrade(source, root, *, commit, bot_id, confirmed=False):
    require(confirmed is True, "PREVIEW_AUTHORIZATION_REQUIRED")
    version = verify_source(source, commit)
    instance = Instance(root)
    settings = instance.load()
    require(settings["host"]["bot_id"] == bot_id, "INSTANCE_MISMATCH")
    require(instance.plan() is None, "MAINTENANCE_ACTIVE", "请从原实例 run.py 执行 maintenance resume")
    folder = instance.path("app/" + settings["app_version"])
    manifest = verify_directory(folder, parse_json((folder / "manifest.json").read_bytes()))
    require((folder / "run.py").read_bytes() == instance.path("run.py").read_bytes(), "LAUNCHER_INCOMPATIBLE")
    # The existing coordinator owns the backup, drain, commit decision and recovery journal.
    # This script never rewrites the installed source version or copies over the live database.
    with tempfile.TemporaryDirectory(prefix="social-lurker-preview-") as directory:
        output = Path(directory).resolve()
        descriptor = build(source, output, version=version, commit=commit)
        # Verify the built snapshot too, in case files changed after the initial checkout check.
        for name, hashed in descriptor["manifest"]["files"].items():
            require(
                digest(git(source, "show", f"{commit}:{source_path(name)}")) == hashed,
                "PREVIEW_COMMIT_MISMATCH",
            )
        worker = (
            "import json,pathlib,sys;sys.path.insert(0,sys.argv[1]);"
            "from social_lurker.config import Instance;"
            "from social_lurker.lifecycle import Lifecycle;"
            "from social_lurker.errors import LurkerError;"
            "d=json.loads(pathlib.Path(sys.argv[3]).read_text());"
            "d['archive_url']='local-pinned-preview';"
            "raw=pathlib.Path(sys.argv[4]).read_bytes();"
            "\ntry:\n r=Lifecycle(Instance(sys.argv[2])).begin(d,confirmed=True,read=lambda _:raw);"
            "print(json.dumps({'protocol':1,'ok':True,'result':r}))"
            "\nexcept LurkerError as e:\n print(json.dumps({'protocol':1,'ok':False,'error':{'code':e.code}}));"
            "raise SystemExit(2)"
        )
        process = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                worker,
                str(folder),
                str(instance.root),
                str(output / "release-manifest.json"),
                str(output / f"social-lurker-{version}.tar.gz"),
            ],
            capture_output=True,
            timeout=180,
        )
    envelope = parse_json(process.stdout)
    if envelope.get("ok") and envelope.get("result", {}).get("reenter"):
        process = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(instance.path("run.py")),
                "--instance",
                str(instance.root),
                "maintenance",
                "resume",
            ],
            input=b'{"protocol":1}',
            capture_output=True,
            timeout=180,
        )
        envelope = parse_json(process.stdout)
    return {
        **envelope,
        "delivery_channel": "development_preview",
        "source_version": manifest["version"],
        "target_version": version,
        "target_commit": commit,
        "stable_release_published": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--commit", required=True, help="Exact authorized 40-character Git commit")
    parser.add_argument("--confirm-preview", action="store_true")
    args = parser.parse_args()
    try:
        result = upgrade(
            ROOT, args.instance, commit=args.commit, bot_id=args.bot_id, confirmed=args.confirm_preview
        )
    except LurkerError as error:
        result = {"protocol": 1, "ok": False, "error": {"code": error.code}}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        result = {
            "protocol": 1,
            "ok": False,
            "error": {"code": "PREVIEW_UPGRADE_INCOMPLETE"},
            "next_action": "核对本实例 maintenance.json；存在时仅使用 run.py maintenance resume",
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
