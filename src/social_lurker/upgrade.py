"""Explicit release discovery and installation transaction; current.json is the commit point."""

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .db import Database
from .errors import LurkerError, require
from .package import extract_package, validate_manifest, verify_directory, version_key
from .profiles import FileRegistry
from .util import atomic_json, digest, read_json, sync_dir, sync_tree

REPO = "shishengkai/social-lurker"
GITHUB = "https://api.github.com/repos/" + REPO


class GitHub:
    def get(self, path):
        require(path.startswith("/") and ".." not in path, "UPGRADE_FAILED")
        try:
            request = urllib.request.Request(
                GITHUB + path,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "social-lurker"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except (OSError, ValueError):
            raise LurkerError("UPGRADE_FAILED") from None

    def releases(self):
        result = []
        for page in range(1, 21):
            chunk = self.get(f"/releases?per_page=100&page={page}")
            require(isinstance(chunk, list), "UPGRADE_FAILED")
            result.extend(chunk)
            if len(chunk) < 100:
                return result
        raise LurkerError("UPGRADE_FAILED")

    def tag_sha(self, tag):
        data = self.get("/git/ref/tags/" + tag)
        for _ in range(5):
            require(isinstance(data, dict) and isinstance(data.get("object"), dict), "UPGRADE_FAILED")
            obj = data["object"]
            require(
                isinstance(obj.get("sha"), str) and re.fullmatch(r"[a-f0-9]{40}", obj["sha"]),
                "UPGRADE_FAILED",
            )
            if obj.get("type") == "commit":
                return obj["sha"]
            require(obj.get("type") == "tag", "UPGRADE_FAILED")
            data = self.get("/git/tags/" + obj["sha"])
        raise LurkerError("UPGRADE_FAILED")

    def download(self, asset, target):
        # No TikHub headers or other credentials are used for software downloads.
        try:
            request = urllib.request.Request(
                asset["browser_download_url"], headers={"User-Agent": "social-lurker"}
            )
            with urllib.request.urlopen(request, timeout=30) as response, Path(target).open("xb") as out:
                remaining = 32 * 1024 * 1024
                while block := response.read(min(65536, remaining + 1)):
                    remaining -= len(block)
                    require(remaining >= 0, "UPGRADE_FAILED")
                    out.write(block)
            require(digest(target) == asset["digest"].removeprefix("sha256:"), "UPGRADE_FAILED")
        except (OSError, KeyError):
            raise LurkerError("UPGRADE_FAILED") from None


def candidate(client, version=None):
    choices = []
    for release in client.releases():
        require(isinstance(release, dict), "UPGRADE_FAILED")
        tag = release.get("tag_name", "")
        if release.get("draft") is False and release.get("prerelease") is False and isinstance(tag, str):
            if re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag):
                if version is None or tag == "v" + version:
                    choices.append(release)
    if not choices:
        require(version is None, "UPGRADE_FAILED")
        return None
    release = max(choices, key=lambda r: version_key(r["tag_name"][1:]))
    require(release.get("immutable") is True, "UPGRADE_FAILED")
    v = release["tag_name"][1:]
    sha = client.tag_sha(release["tag_name"])
    # Immutable historical releases keep their original asset names.
    assets = release.get("assets")
    require(isinstance(assets, list) and all(isinstance(a, dict) for a in assets), "UPGRADE_FAILED")
    prefix = "social-lurker"
    if version_key(v) <= version_key("0.5.1") and not any(
        a.get("name") == f"{prefix}-{v}.tar.gz" for a in assets
    ):
        prefix += "-cli"
    expected = {f"{prefix}-{v}.tar.gz", f"{prefix}-{v}.manifest.json"}
    assets = release.get("assets")
    require(isinstance(assets, list), "UPGRADE_FAILED")
    selected = {}
    for asset in assets:
        require(isinstance(asset, dict), "UPGRADE_FAILED")
        if asset.get("name") in expected:
            require(
                asset["name"] not in selected
                and isinstance(asset.get("digest"), str)
                and re.fullmatch(r"sha256:[a-f0-9]{64}", asset["digest"]),
                "UPGRADE_FAILED",
            )
            require(
                asset.get("browser_download_url")
                == f"https://github.com/{REPO}/releases/download/v{v}/{asset['name']}",
                "UPGRADE_FAILED",
            )
            selected[asset["name"]] = asset
    require(set(selected) == expected, "UPGRADE_FAILED")
    with tempfile.TemporaryDirectory(prefix="social-lurker-release-") as tmp:
        p = Path(tmp) / "manifest.json"
        client.download(selected[f"{prefix}-{v}.manifest.json"], p)
        manifest = validate_manifest(read_json(p, "UPGRADE_FAILED"), version=v, sha=sha, stable=True)
    return {
        "version": v,
        "git_sha": sha,
        "manifest": manifest,
        "manifest_sha256": selected[f"{prefix}-{v}.manifest.json"]["digest"][7:],
        "asset": selected[f"{prefix}-{v}.tar.gz"],
    }


def current(root):
    root = Path(root)
    p = read_json(root / "current.json", "UPGRADE_FAILED")
    require(
        isinstance(p, dict) and p.get("product") == "social-lurker" and p.get("distribution") == "cli",
        "UPGRADE_FAILED",
    )
    version_key(p.get("version"))
    require(
        isinstance(p.get("git_sha"), str) and re.fullmatch(r"[a-f0-9]{40}", p["git_sha"]), "UPGRADE_FAILED"
    )
    m = verify_directory(root / "versions" / p["version"], version=p["version"], sha=p.get("git_sha"))
    require(
        p.get("manifest_sha256") == digest(root / "versions" / p["version"] / "manifest.json")
        and isinstance(p.get("data_root"), str)
        and Path(p["data_root"]).is_absolute(),
        "UPGRADE_FAILED",
    )
    return p, m


def check(root, client=None):
    pointer, _ = current(root)
    selected = candidate(client or GitHub())
    return {
        "current_version": pointer["version"],
        "latest_version": selected["version"] if selected else None,
        "update_available": bool(
            selected and version_key(selected["version"]) > version_key(pointer["version"])
        ),
    }


def candidate_check(root, manifest, profiles):
    # Candidate code runs only after complete validation, without emitting its output to the user.
    code = (
        "import sys;sys.path.insert(0,sys.argv[1]);from social_lurker import __version__;"
        "from social_lurker.db import Database,SCHEMA_VERSION;"
        "from social_lurker.cli import parser;parser();"
        "assert __version__==sys.argv[2];assert SCHEMA_VERSION==int(sys.argv[3]);"
        "\nif len(sys.argv)>4:\n d=Database(sys.argv[4],sys.argv[5],create=False);d.close()"
    )
    for profile in profiles or [None]:
        args = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            code,
            str(root / "src"),
            manifest["version"],
            str(manifest["schema_target"]),
        ]
        if profile:
            args += [str(profile.db_path), profile.profile_id]
        try:
            result = subprocess.run(args, capture_output=True, timeout=30)
            require(result.returncode == 0, "UPGRADE_FAILED")
        except (OSError, subprocess.SubprocessError):
            raise LurkerError("UPGRADE_FAILED") from None


def migrate(path, pid, source, manifest, version_root):
    db = Database(path, pid, create=False, supported_schema=source)
    try:
        while source < manifest["schema_target"]:
            name = manifest.get("migrations", {}).get(str(source))
            require(name is not None, "UPGRADE_FAILED")
            sql = (version_root / name).read_text()
            # Declarative first-generation migrations are additive; no implicit data deletion.
            statements, pending = [], ""
            for character in sql:
                pending += character
                if character == ";" and sqlite3.complete_statement(pending):
                    statements.append(pending.strip())
                    pending = ""
            require(not pending.strip(), "UPGRADE_FAILED")
            with db.transaction() as conn:
                for statement in statements:
                    require(
                        re.match(
                            r"(?is)^ALTER\s+TABLE\s+(authors|works)\s+ADD\s+(COLUMN\s+)?\w+\s+", statement
                        )
                        or re.match(r"(?is)^CREATE\s+(UNIQUE\s+)?INDEX\s+", statement),
                        "UPGRADE_FAILED",
                    )
                    conn.execute(statement)
                source += 1
                conn.execute(f"PRAGMA user_version={source}")
            db.validate(pid, source)
    finally:
        db.close()


def archive_plan(root, plan):
    atomic_json(root / "backups" / plan["id"] / "completed.json", plan)
    (root / "upgrade-state.json").unlink()
    sync_dir(root)


def profile_snapshot(data_root):
    registry = FileRegistry(data_root)
    require(registry.path.exists(), "UPGRADE_FAILED")
    return registry, registry.list()


def restore_before_commit(root, plan):
    # Validate all backups before restoring any; do not infer target paths from arbitrary plan strings.
    registry, profiles = profile_snapshot(plan["old"]["data_root"])
    require([p.profile_id for p in profiles] == [p["id"] for p in plan["profiles"]], "UPGRADE_FAILED")
    backup = root / "backups" / plan["id"]
    if plan["registry_sha256"] is None:
        require(not registry.path.exists() and not profiles, "UPGRADE_FAILED")
    else:
        require(
            digest(backup / "profiles.json") == plan["registry_sha256"]
            and digest(registry.path) == plan["registry_sha256"],
            "UPGRADE_FAILED",
        )
    for profile, entry in zip(profiles, plan["profiles"], strict=True):
        p = backup / profile.backup_name
        require(digest(p) == entry["sha256"], "UPGRADE_FAILED")
        db = Database(p, profile.profile_id, create=False, supported_schema=entry["schema"])
        db.close()
    for profile in profiles:
        with sqlite3.connect(backup / profile.backup_name) as src:
            with sqlite3.connect(profile.db_path) as dst:
                src.backup(dst)
    atomic_json(root / "current.json", plan["old"])
    plan["stage"] = "rolled_back"
    archive_plan(root, plan)


def recover(root):
    root = Path(root)
    state = root / "upgrade-state.json"
    if not state.exists():
        return
    plan = read_json(state, "UPGRADE_FAILED")
    require(
        isinstance(plan, dict)
        and re.fullmatch(r"[a-f0-9]{32}", str(plan.get("id", "")))
        and plan.get("stage") in {"prepared", "migrating", "switching", "committed"}
        and isinstance(plan.get("profiles"), list)
        and isinstance(plan.get("old"), dict)
        and isinstance(plan.get("target"), dict),
        "UPGRADE_FAILED",
    )
    pointer, _ = current(root)
    if pointer == plan["old"]:
        restore_before_commit(root, plan)
    elif pointer == plan["target"]:
        # Commit point crossed: never overwrite new facts with old database snapshots.
        m = verify_directory(
            root / "versions" / pointer["version"], version=pointer["version"], sha=pointer["git_sha"]
        )
        registry, profiles = profile_snapshot(pointer["data_root"])
        require([p.profile_id for p in profiles] == [p["id"] for p in plan["profiles"]], "UPGRADE_FAILED")
        candidate_check(root / "versions" / pointer["version"], m, profiles)
        plan["stage"] = "committed"
        archive_plan(root, plan)
    else:
        raise LurkerError("UPGRADE_FAILED")


def apply_package(root, archive, *, expected=None, fault=lambda stage: None):
    """Caller holds the installation exclusive lock. Backup all profiles before any migration."""
    root = Path(root)
    recover(root)
    old, old_manifest = current(root)
    expected = expected or {}
    with tempfile.TemporaryDirectory(dir=root, prefix=".upgrade-") as tmp:
        unpacked = Path(tmp) / "candidate"
        manifest = extract_package(
            archive,
            unpacked,
            version=expected.get("version"),
            sha=expected.get("git_sha"),
            stable=bool(expected),
        )
        if expected:
            require(digest(unpacked / "manifest.json") == expected["manifest_sha256"], "UPGRADE_FAILED")
        v = manifest["version"]
        if v == old["version"]:
            require(manifest == old_manifest, "UPGRADE_FAILED")
            return {"previous_version": v, "current_version": v, "changed": False, "migration_results": []}
        require(version_key(v) > version_key(old["version"]), "UPGRADE_FAILED")
        target_root = root / "versions" / v
        if target_root.exists():
            require(verify_directory(target_root) == manifest, "UPGRADE_FAILED")
        else:
            shutil.copytree(unpacked, target_root)
            for path in target_root.rglob("*"):
                if path.is_file():
                    path.chmod(0o444)
            sync_tree(target_root)
        fault("unpacked")
    registry, profiles = profile_snapshot(old["data_root"])
    backup_id = uuid.uuid4().hex
    backup = root / "backups" / backup_id
    backup.mkdir(parents=True)
    if profiles or registry.path.exists():
        shutil.copyfile(registry.path, backup / "profiles.json")
        registry_sha = digest(backup / "profiles.json")
    else:
        registry_sha = None
    entries = []
    for profile in profiles:
        # Unused registered profiles still have a fresh, identified database in the backup set.
        db = Database(profile.db_path, profile.profile_id, supported_schema=old_manifest["schema_target"])
        try:
            schema = db.connection.execute("PRAGMA user_version").fetchone()[0]
            require(manifest["schema_min"] <= schema <= manifest["schema_target"], "UPGRADE_FAILED")
            p = backup / profile.backup_name
            with sqlite3.connect(p) as dst:
                db.connection.backup(dst)
            entries.append({"id": profile.profile_id, "schema": schema, "sha256": digest(p)})
        finally:
            db.close()
    shutil.copyfile(root / "current.json", backup / "current.json")
    sync_tree(backup)
    fault("backed_up")
    target = {
        **old,
        "version": v,
        "git_sha": manifest["git_sha"],
        "manifest_sha256": digest(target_root / "manifest.json"),
    }
    plan = {
        "id": backup_id,
        "stage": "prepared",
        "old": old,
        "target": target,
        "registry_sha256": registry_sha,
        "profiles": entries,
        "migrated": [],
    }
    state = root / "upgrade-state.json"
    atomic_json(state, plan)
    try:
        fault("prepared")
        for profile, entry in zip(profiles, entries, strict=True):
            plan["stage"] = "migrating"
            atomic_json(state, plan)
            migrate(profile.db_path, profile.profile_id, entry["schema"], manifest, target_root)
            plan["migrated"].append(profile.profile_id)
            atomic_json(state, plan)
            fault("migrated:" + profile.profile_id)
        candidate_check(target_root, manifest, profiles)
        plan["stage"] = "switching"
        atomic_json(state, plan)
        fault("switching")
        atomic_json(root / "current.json", target)
        fault("pointer_switched")
        plan["stage"] = "committed"
        atomic_json(state, plan)
        fault("committed")
        archive_plan(root, plan)
        fault("archived")
    except Exception:
        # Recovery determines direction from the pointer, not from an in-memory stage.
        if state.exists():
            recover(root)
        raise LurkerError("UPGRADE_FAILED") from None
    return {
        "previous_version": old["version"],
        "current_version": v,
        "changed": True,
        "migration_results": [
            {"profile_id": p.profile_id, "schema_version": manifest["schema_target"]} for p in profiles
        ],
    }


def apply(root, version, client=None):
    client = client or GitHub()
    selected = candidate(client, version)
    with tempfile.TemporaryDirectory(prefix="social-lurker-download-") as tmp:
        archive = Path(tmp) / "package.tar.gz"
        client.download(selected["asset"], archive)
        return apply_package(root, archive, expected=selected)
