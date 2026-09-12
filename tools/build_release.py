#!/usr/bin/env python3
"""Build a deterministic asset from a clean source commit; never tag/push/publish."""

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", type=Path, default=REPO / "dist")
    parser.add_argument("--platform", choices=("x86_64", "aarch64"), action="append", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("version must be SemVer")
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True)
    if status.strip():
        parser.error("commit the reviewed source first; release packages require a clean source commit")
    import tomllib

    if tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"] != args.version:
        parser.error("pyproject version must match the release")
    init_text = (REPO / "src/social_lurker/__init__.py").read_text()
    if not re.search(r"__version__\s*=\s*[\"\']" + re.escape(args.version) + r"[\"\']", init_text):
        parser.error("__version__ must match the release")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    epoch = int(
        subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=REPO, text=True).strip()
    )
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO).decode().split("\0")
    data = {}
    for name in paths:
        if name.startswith("src/social_lurker/"):
            dest = "app/" + name.removeprefix("src/")
        elif name == "tools/launcher.py":
            dest = "launcher.py"
        elif name.startswith(("vendor/", "skills/")) or name in ("requirements-runtime.txt", "LICENSE"):
            dest = name
        else:
            continue
        path = REPO / name
        if path.is_symlink():
            parser.error("symlinks are not release assets")
        data[dest] = path.read_bytes()
    manifest = {
        "project": "social-lurker",
        "authority": "github.com/shishengkai/social-lurker",
        "version": args.version,
        "channel": "stable",
        "source_commit": sha,
        "released_at": datetime.fromtimestamp(epoch, UTC).isoformat(),
        "summary": args.summary,
        "platforms": [["linux", p] for p in sorted(set(args.platform))],
        "python": ">=3.12,<4",
        "launcher_protocol": {"min": 1, "max": 1},
        "schema_version": 1,
        "migrate_from": [1],
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(data.items())},
    }
    data["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, contents in sorted(data.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(contents), epoch, 0o644
            archive.addfile(info, io.BytesIO(contents))
    args.output.mkdir(parents=True, exist_ok=True)
    filename = args.output / f"social-lurker-{args.version}.tar.gz"
    with filename.open("wb") as target:
        with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=epoch) as compressed:
            compressed.write(raw.getvalue())
    descriptor = {"manifest": manifest, "archive_sha256": hashlib.sha256(filename.read_bytes()).hexdigest()}
    (args.output / "release-manifest.json").write_text(
        json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "archive": str(filename),
                "source_commit": sha,
                "next": "review descriptor, commit release-manifest.json, then explicitly publish immutable tagged Release",
            }
        )
    )


if __name__ == "__main__":
    main()
