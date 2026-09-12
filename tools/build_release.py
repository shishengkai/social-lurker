# ruff: noqa: E402
#!/usr/bin/env python3
"""Build a bounded, deterministic release from a clean committed source; never publish."""

import argparse
import gzip
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from social_lurker import __version__
from social_lurker.errors import require
from social_lurker.releases import AUTHORITY, package_bytes, validate_manifest
from social_lurker.util import atomic_bytes, canonical, digest, safe_path


def build(source, output, *, version=__version__, commit=None):
    source = safe_path(source)
    output = safe_path(output)
    if commit is None:
        require(
            not subprocess.check_output(["git", "status", "--porcelain"], cwd=source).strip(), "SOURCE_DIRTY"
        )
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    require(not output.is_relative_to(source), "OUTPUT_OUTSIDE_SOURCE_REQUIRED")
    files = package_bytes(source)
    manifest = validate_manifest(
        dict(
            authority=AUTHORITY,
            product="social-lurker-lightweight",
            version=version,
            channel="stable",
            source_commit=commit,
            protocol=1,
            schema_version=1,
            files={k: digest(v) for k, v in files.items()},
        )
    )
    files["manifest.json"] = (canonical(manifest) + "\n").encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o400
                archive.addfile(info, io.BytesIO(data))
    raw = buffer.getvalue()
    output.mkdir(parents=True, exist_ok=True)
    descriptor = {"manifest": manifest, "archive_sha256": digest(raw)}
    atomic_bytes(output / f"social-lurker-{version}.tar.gz", raw)
    atomic_bytes(output / "release-manifest.json", (canonical(descriptor) + "\n").encode())
    return descriptor


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.output), ensure_ascii=False))
