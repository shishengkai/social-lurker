"""Offline child-process fixture. Deliberately absent from distributed CLI packages."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from social_lurker.adapters.base import Author, Page, Work
from social_lurker.cli import main
from social_lurker.config import FileSecrets
from social_lurker.db import Database
from social_lurker.profiles import FileRegistry

root, mode = Path(sys.argv[1]), sys.argv[2]
registry = FileRegistry(root / "data")
if not registry.path.exists():
    profile = registry.create("子进程空间")
    db = Database(profile.db_path, profile.profile_id)
    db.upsert_page(Author("douyin", "a", "甲"), [], baseline=True)
    db.close()
else:
    profile = registry.get("p0001")


class Slow:
    def fetch_page(self, author, cursor):
        with (root / "calls").open("a") as f:
            f.write(str(cursor) + "\n")
            f.flush()
        if cursor is None:
            return Page([Work("douyin", "new", "a")], "second", True)
        (root / "waiting").touch()
        if mode == "pipe":
            deadline = time.monotonic() + 5
            while not (root / "reader_closed").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            # Model a transport's before_request check after an in-flight request has completed.
            from social_lurker.output import Output

            Output().ensure_open()
            return Page([Work("douyin", "second", "a")], "third", True)
        time.sleep(5)
        return Page([])

    def enrich_metadata(self, author, work):
        return work


fmt = "json" if mode == "json" else "jsonl"
exit_code = main(
    [
        "--profile",
        profile.profile_id,
        "--install-root",
        str(root / "install"),
        "--data-root",
        str(root / "data"),
        "--format",
        fmt,
        "check",
    ],
    registry=registry,
    secrets=FileSecrets(root / "none", {"TIKHUB_API_KEY": "fixture"}),
    adapters={"douyin": Slow()},
)
if exit_code == 141:
    # Suppress interpreter-shutdown flushing to the already closed fixture pipe.
    sys.stdout = open(os.devnull, "w")
raise SystemExit(exit_code)
