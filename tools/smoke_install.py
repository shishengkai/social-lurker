#!/usr/bin/env python3
"""Real isolated installation/repeat/new-Bot smoke. No social API, ASR or native messages."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]


def run(root):
    assert not (root / "bots").exists(), "Use a fresh test directory"
    first, second = str(uuid.uuid4()), str(uuid.uuid4())

    def install(ident):
        proc = subprocess.run(
            [
                sys.executable,
                str(SOURCE / "install.py"),
                "--root",
                str(root),
                "--instance",
                ident,
                "--channel",
                "preview",
                "--allow-working-tree",
            ],
            capture_output=True,
            text=True,
            timeout=2400,
        )
        if proc.returncode:
            raise RuntimeError(
                proc.stdout
            )  # Bootstrap output never contains credentials or raw subprocess logs.
        return json.loads(proc.stdout)

    initial = install(first)
    assert initial["runtime_version"] == "0.2.0-dev" and not initial["resumed"]
    package = root / "runtime/releases" / initial["runtime_version"]
    manifest = (package / "manifest.json").read_bytes()
    python_mtime = (package / ".venv/bin/python").lstat().st_mtime_ns
    for name in ("social-lurker", "social-lurker-setup", "social-lurker-upgrader"):
        assert (package / "skills" / name / "SKILL.md").is_file()
    data = root / "bots" / first
    settings = json.loads((data / "settings.json").read_text())
    settings["check_interval_minutes"] = 45
    (data / "settings.json").write_text(json.dumps(settings))
    (data / ".env").write_text("TIKHUB_API_KEY=fixture-only\nFAL_KEY=fixture-only\n")
    before = {name: (data / name).read_bytes() for name in ("settings.json", ".env")}
    with sqlite3.connect(data / "lurker.sqlite3") as db:
        db.execute(
            "INSERT INTO accounts(platform,platform_account_id,display_name,profile_url,tracking_state,watch_started_at,created_at,updated_at) VALUES('douyin','fixture','test','https://example.com','stopped',?,?,?)",
            (time.time(),) * 3,
        )
        db.execute(
            "INSERT INTO works(account_id,platform_work_id,source_url,first_seen_at,raw_transcript_text,transcript_text,processing_state,created_at,updated_at) VALUES(1,'fixture','https://example.com',?,'原稿保留','全文保留','ready',?,?)",
            (time.time(),) * 3,
        )
    repeat = install(first)
    assert repeat["resumed"] and repeat["result"]["routine"]["interval_minutes"] == 45
    assert before == {name: (data / name).read_bytes() for name in before}
    other = install(second)
    assert not other["resumed"] and other["runtime_version"] == initial["runtime_version"]
    assert manifest == (package / "manifest.json").read_bytes()
    assert python_mtime == (package / ".venv/bin/python").lstat().st_mtime_ns
    assert len(list((root / "runtime/releases").iterdir())) == 1
    with sqlite3.connect(data / "lurker.sqlite3") as db:
        assert db.execute("SELECT raw_transcript_text,transcript_text FROM works").fetchone() == (
            "原稿保留",
            "全文保留",
        )
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(root / "bots" / second / "lurker.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM works").fetchone()[0] == 0
    assert "fixture-only" not in (root / "bots" / second / ".env").read_text()
    req = {"protocol_version": 1, "request_id": "smoke-doctor", "payload": {}}
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "runtime/launcher.py"),
            "--instance",
            first,
            "doctor",
            "--request-stdin",
        ],
        input=json.dumps(req),
        capture_output=True,
        text=True,
        env=dict(os.environ, PATH="/usr/bin:/bin"),
        timeout=90,
    )
    report = json.loads(proc.stdout)
    assert report["result"]["local_ready"], report
    result = {
        "installation": True,
        "repeat_preserves_data": True,
        "new_bot_reuses_package": True,
        "independent_credentials": True,
        "scheduled_path": True,
        "skills": 3,
        "runtime_version": initial["runtime_version"],
        "checks": report["result"]["checks"],
        "grok_acceptance_pending": True,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        run(args.root.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="social-lurker-install-smoke-") as tmp:
            run(Path(tmp).resolve())
