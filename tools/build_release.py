"""Build a local content-pinned CLI archive; never publish or touch Git state."""

import argparse
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from social_lurker import __version__
from social_lurker.util import digest


def build(root, output, *, development=False):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True
    )
    if dirty and not development:
        raise RuntimeError("正式构建要求干净工作区；本地冒烟请显式传 --development")
    files = sorted(
        [root / "install.py", root / "tools/launcher.py", root / "LICENSE"]
        + [p for p in (root / "src").rglob("*") if p.suffix in {".py", ".sql"} and p.is_file()]
    )
    manifest = {
        "product": "social-lurker",
        "distribution": "cli",
        "version": __version__,
        "git_sha": sha,
        "source_state": "local-development" if development else "clean",
        "python_min": [3, 12],
        "schema_min": 1,
        "schema_max": 1,
        "schema_target": 1,
        "migrations": {},
        "files": {p.relative_to(root).as_posix(): digest(p) for p in files},
    }
    path = output / f"social-lurker-{__version__}.manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n")
    archive = output / f"social-lurker-{__version__}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for file in files:
            tar.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)
        tar.add(path, arcname="manifest.json", recursive=False)
    return archive


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--development", action="store_true")
    args = p.parse_args()
    print(build(ROOT, args.output, development=args.development))
