#!/usr/bin/env python3
"""Stable entry: validate all files before importing code; hold installation lock throughout."""

import fcntl
import hashlib
import json
import os
import re
import signal
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def verified(root):
    p = json.loads((root / "current.json").read_text())
    assert p["product"] == "social-lurker" and p["distribution"] == "cli"
    assert re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", p["version"])
    version = root / "versions" / p["version"]
    assert not version.is_symlink()
    assert sha(version / "manifest.json") == p["manifest_sha256"]
    m = json.loads((version / "manifest.json").read_text())
    assert m["product"] == p["product"] and m["distribution"] == "cli"
    assert m["version"] == p["version"] and m["git_sha"] == p["git_sha"]
    actual = set()
    for path in version.rglob("*"):
        assert not path.is_symlink()
        if path.is_file():
            actual.add(path.relative_to(version).as_posix())
    assert actual == set(m["files"]) | {"manifest.json"}
    for name, expected in m["files"].items():
        assert name and not name.startswith("/") and ".." not in Path(name).parts and "\\" not in name
        assert sha(version / name) == expected
    return p, version


def run():
    if sys.version_info < (3, 12):
        raise RuntimeError("python version")
    root = Path(__file__).resolve().parents[1]
    with (root / "install.lock").open("a+b") as guard:
        exclusive = "upgrade" in sys.argv[1:] and "apply" in sys.argv[1:]
        pending = (root / "upgrade-state.json").exists()
        try:
            fcntl.flock(guard, (fcntl.LOCK_EX if exclusive or pending else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            return failure("MAINTENANCE_BUSY", 4)
        p, version = verified(root)
        if (root / "upgrade-state.json").exists():
            # The plan may have appeared between the pre-lock snapshot and acquisition.
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return failure("MAINTENANCE_BUSY", 4)
            sys.path.insert(0, str(version / "src"))
            from social_lurker.upgrade import recover

            recover(root)
            sys.path.pop(0)
            for name in list(sys.modules):
                if name == "social_lurker" or name.startswith("social_lurker."):
                    del sys.modules[name]
            if not exclusive:
                fcntl.flock(guard, fcntl.LOCK_SH)
            p, version = verified(root)
        sys.path.insert(0, str(version / "src"))
        from social_lurker.cli import main

        # Callers cannot select a different root through this installed entry.
        args = ["--install-root", str(root), "--data-root", p["data_root"]] + sys.argv[1:]
        return main(args, install_locked=True)


def failure(code, exit_code):
    # Bootstrap cannot import a damaged installation to generate its error protocol.
    import uuid

    public = {
        "schema_version": 1,
        "cli_version": "bootstrap",
        "run_id": str(uuid.uuid4()),
        "command": None,
        "profile_id": None,
    }
    try:
        records = [
            dict(
                public,
                type="error",
                error={
                    "code": code,
                    "message": "安装忙或完整性验证失败",
                    "retryable": code == "MAINTENANCE_BUSY",
                },
            ),
            dict(
                public,
                type="complete",
                status="error",
                scan_complete=None,
                summary=dict.fromkeys(
                    (
                        "authors_total",
                        "authors_succeeded",
                        "authors_failed",
                        "authors_unchecked",
                        "pages_committed",
                        "new_works",
                        "requests",
                    ),
                    0,
                ),
            ),
        ]
        if "--format=json" in sys.argv or any(
            sys.argv[i : i + 2] == ["--format", "json"] for i in range(len(sys.argv))
        ):
            print(
                json.dumps(dict(public, records=records[:1], completion=records[1]), ensure_ascii=False),
                flush=True,
            )
        else:
            for record in records:
                print(json.dumps(record, ensure_ascii=False), flush=True)
    except (OSError, ValueError):
        return 141
    return exit_code


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    try:
        exit_code = run()
    except Exception:
        exit_code = failure("UPGRADE_FAILED", 1)
    if exit_code == 141:
        sys.stdout = open(os.devnull, "w")
    raise SystemExit(exit_code)
