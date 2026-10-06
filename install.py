#!/usr/bin/env python3
"""Run this trusted source installer; candidate assets are validated before execution."""

import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from social_lurker.config import DEFAULT_DATA, DEFAULT_INSTALL
from social_lurker.errors import LurkerError, require
from social_lurker.locks import lock
from social_lurker.package import extract_package
from social_lurker.upgrade import candidate_check
from social_lurker.util import atomic_json, digest, sync_tree


def install(package, install_root, data_root):
    root, data = Path(install_root).expanduser().resolve(), Path(data_root).expanduser().resolve()
    require(root != data and root not in data.parents and data not in root.parents, "CONFIG_INVALID")
    # Reject historical/nonempty directories before creating even a lock file.
    require(not root.exists() or set(p.name for p in root.iterdir()) <= {"install.lock"}, "UPGRADE_FAILED")
    require(not data.exists() or not any(data.iterdir()), "CONFIG_INVALID")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
        # Existing files/instances are never imported, removed, or overwritten.
        require(set(p.name for p in root.iterdir()) == {"install.lock"}, "UPGRADE_FAILED")
        require(not data.exists() or not any(data.iterdir()), "CONFIG_INVALID")
        import tempfile

        with tempfile.TemporaryDirectory(dir=root, prefix=".install-") as tmp:
            candidate = Path(tmp) / "candidate"
            manifest = extract_package(package, candidate)
            require(manifest["schema_target"] == 1, "UPGRADE_FAILED")
            candidate_check(candidate, manifest, [])
            version_root = root / "versions" / manifest["version"]
            version_root.parent.mkdir()
            shutil.copytree(candidate, version_root)
            for path in version_root.rglob("*"):
                if path.is_file():
                    os.chmod(path, 0o444)
            (root / "bin").mkdir()
            shutil.copyfile(candidate / "tools/launcher.py", root / "bin/launcher.py")
            entry = root / "bin/social-lurker"
            entry.write_text(
                "#!/bin/sh\nexec "
                + shlex.quote(str(Path(sys.executable).resolve()))
                + " -I -B "
                + shlex.quote(str(root / "bin/launcher.py"))
                + ' "$@"\n'
            )
            os.chmod(root / "bin/social-lurker", 0o755)
            sync_tree(version_root)
            sync_tree(root / "bin")
            data.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_json(data / "profiles.json", {"schema_version": 1, "next_id": 1, "profiles": []})
            atomic_json(
                root / "current.json",
                {
                    "product": "social-lurker",
                    "distribution": "cli",
                    "version": manifest["version"],
                    "git_sha": manifest["git_sha"],
                    "data_root": str(data),
                    "manifest_sha256": digest(version_root / "manifest.json"),
                },
            )
    return {"version": manifest["version"], "source_state": manifest["source_state"], "installed": True}


def main():
    p = argparse.ArgumentParser(description="在独立空白目录安装已验证的纯 CLI 包")
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL)
    p.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    args = p.parse_args()
    import json

    try:
        print(json.dumps(install(args.package, args.install_root, args.data_root), ensure_ascii=False))
    except (LurkerError, OSError):
        print(json.dumps({"installed": False, "error": "安装目录或软件包验证失败"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
