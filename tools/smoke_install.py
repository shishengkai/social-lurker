#!/usr/bin/env python3
"""Offline preview installation, stable entry, repeat and Bot isolation in a temporary directory."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    p = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise RuntimeError(p.stdout)
    response = json.loads(p.stdout)
    assert response["ok"], response
    return response["result"]


def main():
    with tempfile.TemporaryDirectory(prefix="lurker-r1-smoke-") as folder:
        a = Path(folder).resolve() / "bot-a"
        b = Path(folder).resolve() / "bot-b"
        first = run(
            ROOT / "install.py",
            "--instance",
            a,
            "--bot-id",
            "fixture-a",
            "--allow-working-tree",
            "--system-only",
        )
        assert (
            first["created"]
            and first["star_event"] == "install_completed"
            and first["setup"]["mode"] == "foreground_only"
        )
        (a / ".env").write_text("TIKHUB_API_KEY=fixture-preserve\n")
        second = run(
            ROOT / "install.py",
            "--instance",
            a,
            "--bot-id",
            "fixture-a",
            "--allow-working-tree",
            "--system-only",
        )
        assert not second["created"] and second["star_event"] is None
        assert (a / ".env").read_text() == "TIKHUB_API_KEY=fixture-preserve\n"
        third = run(
            ROOT / "install.py",
            "--instance",
            b,
            "--bot-id",
            "fixture-b",
            "--allow-working-tree",
            "--system-only",
        )
        assert (
            third["instance_id"] != first["instance_id"]
            and "fixture-preserve" not in (b / ".env").read_text()
        )
        status = run(a / "run.py", "--instance", a, "--json", '{"protocol":1}', "status")
        assert status["updates"] == {} and status["watches"] == []
        assert len(list((a / "app/0.3.0/skills").glob("*/SKILL.md"))) == 2
        print(
            json.dumps(
                {
                    "ok": True,
                    "version": status["version"],
                    "instances": 2,
                    "repeat_preserved_credentials": True,
                    "real_api_calls": 0,
                    "host_messages": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
