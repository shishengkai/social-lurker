"""Validate packages without importing or executing any candidate code."""

import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .errors import LurkerError, require
from .util import digest, read_json

VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA = re.compile(r"[a-f0-9]{40}")
HASH = re.compile(r"[a-f0-9]{64}")
REQUIRED = {
    "install.py",
    "tools/launcher.py",
    "src/social_lurker/cli.py",
    "src/social_lurker/db.py",
    "src/social_lurker/schema.sql",
    "src/social_lurker/__init__.py",
}


def version_key(value):
    require(isinstance(value, str) and VERSION.fullmatch(value), "UPGRADE_FAILED")
    return tuple(map(int, value.split(".")))


def safe_path(value):
    require(
        isinstance(value, str) and bool(value) and "\\" not in value and "\x00" not in value, "UPGRADE_FAILED"
    )
    p = PurePosixPath(value)
    require(not p.is_absolute() and ".." not in p.parts and str(p) == value, "UPGRADE_FAILED")
    return p


def validate_manifest(m, *, version=None, sha=None, stable=False):
    require(
        isinstance(m, dict) and m.get("product") == "social-lurker" and m.get("distribution") == "cli",
        "UPGRADE_FAILED",
    )
    version_key(m.get("version"))
    require(isinstance(m.get("git_sha"), str) and SHA.fullmatch(m["git_sha"]), "UPGRADE_FAILED")
    require(version is None or m["version"] == version, "UPGRADE_FAILED")
    require(sha is None or m["git_sha"] == sha, "UPGRADE_FAILED")
    require(m.get("python_min") == [3, 12] and sys.version_info >= (3, 12), "UPGRADE_FAILED")
    require(
        m.get("source_state") in {"clean", "local-development"}
        and (not stable or m["source_state"] == "clean"),
        "UPGRADE_FAILED",
    )
    for name in ("schema_min", "schema_max", "schema_target"):
        require(type(m.get(name)) is int and 1 <= m[name] <= 100, "UPGRADE_FAILED")
    require(m["schema_min"] <= m["schema_target"] <= m["schema_max"], "UPGRADE_FAILED")
    files = m.get("files")
    require(
        isinstance(files, dict) and REQUIRED <= set(files) and "manifest.json" not in files, "UPGRADE_FAILED"
    )
    require(len(files) <= 1000, "UPGRADE_FAILED")
    for name, sha256 in files.items():
        safe_path(name)
        require(isinstance(sha256, str) and HASH.fullmatch(sha256), "UPGRADE_FAILED")
    migrations = m.get("migrations", {})
    require(isinstance(migrations, dict), "UPGRADE_FAILED")
    for source, name in migrations.items():
        require(
            isinstance(source, str)
            and source.isdigit()
            and name in files
            and name.startswith("migrations/")
            and name.endswith(".sql"),
            "UPGRADE_FAILED",
        )
    return m


def verify_directory(root, **expected):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "UPGRADE_FAILED")
    m = validate_manifest(read_json(root / "manifest.json", "UPGRADE_FAILED"), **expected)
    actual = set()
    for p in root.rglob("*"):
        require(not p.is_symlink(), "UPGRADE_FAILED")
        if p.is_file():
            actual.add(p.relative_to(root).as_posix())
        else:
            require(p.is_dir(), "UPGRADE_FAILED")
    require(actual == set(m["files"]) | {"manifest.json"}, "UPGRADE_FAILED")
    for name, sha256 in m["files"].items():
        require(digest(root / name) == sha256, "UPGRADE_FAILED")
    return m


def extract_package(archive, destination, **expected):
    destination = Path(destination)
    require(not destination.exists(), "UPGRADE_FAILED")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".candidate-") as temp:
        root = Path(temp)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = tar.getmembers()
                require(
                    len(members) <= 1001 and sum(m.size for m in members) <= 32 * 1024 * 1024,
                    "UPGRADE_FAILED",
                )
                names = [m.name for m in members]
                require(len(names) == len(set(names)), "UPGRADE_FAILED")
                for member in members:
                    safe_path(member.name)
                    require(member.isfile(), "UPGRADE_FAILED")
                    target = root / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open("xb") as out:
                        shutil.copyfileobj(source, out)
                m = verify_directory(root, **expected)
                shutil.copytree(root, destination)
                return m
        except (tarfile.TarError, OSError, ValueError):
            raise LurkerError("UPGRADE_FAILED") from None
