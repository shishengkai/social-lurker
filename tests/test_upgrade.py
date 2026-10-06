import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from conftest import ROOT, work

from social_lurker.adapters.base import Author
from social_lurker.db import Database
from social_lurker.errors import LurkerError
from social_lurker.locks import lock
from social_lurker.package import extract_package, verify_directory
from social_lurker.profiles import FileRegistry
from social_lurker.upgrade import REPO, apply, apply_package, candidate, check, current, recover
from social_lurker.util import atomic_json, digest


def make_package(tmp, version="0.5.0", schema=1, *, overrides=None, bad_sql=False, stable=False):
    tmp.mkdir(parents=True, exist_ok=True)
    files = {}
    for path in [ROOT / "install.py", ROOT / "tools/launcher.py", ROOT / "LICENSE"] + list(
        (ROOT / "src").rglob("*")
    ):
        if path.is_file() and path.suffix in {".py", ".sql"} or path.name == "LICENSE":
            data = path.read_bytes()
            if path.name == "__init__.py" and path.parent.name == "social_lurker":
                data = data.replace(b'__version__ = "0.5.0"', f'__version__ = "{version}"'.encode())
            if path.name == "db.py":
                data = data.replace(b"SCHEMA_VERSION = 1", f"SCHEMA_VERSION = {schema}".encode())
            files[path.relative_to(ROOT).as_posix()] = data
    if schema == 2:
        files["migrations/1.sql"] = (
            b"ALTER TABLE works ADD COLUMN aux TEXT;"
            if not bad_sql
            else b"ALTER TABLE missing ADD COLUMN aux TEXT;"
        )
    manifest = {
        "product": "social-lurker",
        "distribution": "cli",
        "version": version,
        "git_sha": "a" * 40,
        "python_min": [3, 12],
        "source_state": "clean" if stable else "local-development",
        "schema_min": 1,
        "schema_max": schema,
        "schema_target": schema,
        "migrations": {"1": "migrations/1.sql"} if schema == 2 else {},
        "files": {n: hashlib.sha256(v).hexdigest() for n, v in files.items()},
    }
    manifest.update(overrides or {})
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    archive = tmp / f"social-lurker-cli-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in {**files, "manifest.json": manifest_bytes}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    manifest_path = tmp / f"social-lurker-cli-{version}.manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    return archive, manifest_path


@pytest.fixture
def installed(tmp_path, installer):
    archive, _ = make_package(tmp_path / "pkg")
    root, data = tmp_path / "install", tmp_path / "data"
    installer(archive, root, data)
    registry = FileRegistry(data)
    for label in ("甲", "乙"):
        profile = registry.create(label)
        db = Database(profile.db_path, profile.profile_id)
        db.upsert_page(Author("douyin", "author-a", "甲"), [work("old")], baseline=True)
        db.close()
    return root, registry, archive


def run_installed(root, args):
    return subprocess.run(
        [str(root / "bin/social-lurker"), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_clean_install_entry_and_tamper_rejected(installed):
    root, _, _ = installed
    result = run_installed(root, ["--version"])
    assert result.returncode == 0 and result.stdout.strip() == "0.5.0" and result.stderr == ""
    result = run_installed(root, ["--profile", "p0001", "list"])
    assert (
        result.returncode == 0
        and json.loads(result.stdout.splitlines()[0])["payload"]["authors"][0]["works_count"] == 1
    )
    source = root / "versions/0.5.0/src/social_lurker/cli.py"
    source.chmod(0o644)
    source.write_text(source.read_text() + "\n# tampered\n")
    result = run_installed(root, ["--format=json", "--profile", "p0001", "check"])
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(result.stdout)["records"][0]["error"]["code"] == "UPGRADE_FAILED"


def test_install_does_not_open_or_touch_old_instance(tmp_path, installer):
    old = tmp_path / "old-instance"
    old.mkdir()
    (old / "settings.json").write_text("historical settings")
    (old / ".env").write_text("private fixture")
    (old / "old.sqlite").write_bytes(b"old db")
    before = {p.name: digest(p) for p in old.iterdir()}
    archive, _ = make_package(tmp_path / "pkg")
    installer(archive, tmp_path / "new-install", tmp_path / "new-data")
    assert before == {p.name: digest(p) for p in old.iterdir()}
    with pytest.raises(LurkerError):
        installer(archive, old, tmp_path / "other-data")
    assert before == {p.name: digest(p) for p in old.iterdir()}


@pytest.mark.parametrize(
    "override",
    [
        {"distribution": "skill"},
        {"git_sha": "main"},
        {"version": "0.5.0-rc1"},
        {"python_min": [3, 15]},
        {"schema_min": 2},
        {"files": {"../escape": "a" * 64}},
    ],
)
def test_invalid_manifest_before_candidate_code(tmp_path, override):
    archive, _ = make_package(tmp_path / "pkg", overrides=override)
    with pytest.raises(LurkerError):
        extract_package(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", "file"),
        ("/absolute", "file"),
        ("link", "symlink"),
        ("src/social_lurker/cli.py", "duplicate"),
        ("unlisted.txt", "file"),
    ],
)
def test_archive_traversal_symlink_extra_duplicate(tmp_path, name, kind):
    archive, _ = make_package(tmp_path / "pkg")
    invalid = tmp_path / "invalid.tar.gz"
    with tarfile.open(archive) as source, tarfile.open(invalid, "w:gz") as target:
        for member in source.getmembers():
            target.addfile(member, source.extractfile(member))
        info = tarfile.TarInfo(name)
        if kind == "symlink":
            info.type, info.linkname = tarfile.SYMTYPE, "../escape"
            target.addfile(info)
        else:
            data = b"extra"
            info.size = len(data)
            target.addfile(info, io.BytesIO(data))
    with pytest.raises(LurkerError):
        extract_package(invalid, tmp_path / "out")


def test_digest_tamper_and_version_identity(tmp_path):
    archive, _ = make_package(tmp_path / "pkg")
    with pytest.raises(LurkerError):
        extract_package(archive, tmp_path / "out", version="0.5.1")
    extract_package(archive, tmp_path / "valid")
    (tmp_path / "valid/LICENSE").write_text("tampered")
    with pytest.raises(LurkerError):
        verify_directory(tmp_path / "valid")


class FakeGitHub:
    def __init__(self, root, *, version="0.5.1", override=None, asset_change=False):
        archive, manifest = make_package(root, version, stable=True, overrides=override)
        self.sources = {p.name: p for p in (archive, manifest)}
        self.asset_change = asset_change
        self.items = [
            {
                "draft": False,
                "prerelease": False,
                "immutable": True,
                "tag_name": "v" + version,
                "assets": [
                    {
                        "name": name,
                        "digest": "sha256:" + digest(path),
                        "browser_download_url": f"https://github.com/{REPO}/releases/download/v{version}/{name}",
                    }
                    for name, path in self.sources.items()
                ],
            }
        ]

    def releases(self):
        return self.items

    def tag_sha(self, tag):
        return "a" * 40

    def download(self, asset, target):
        shutil.copyfile(self.sources[asset["name"]], target)
        if self.asset_change and asset["name"].endswith(".tar.gz"):
            with Path(target).open("ab") as f:
                f.write(b"changed")
        if digest(target) != asset["digest"][7:]:
            raise LurkerError("UPGRADE_FAILED")


def test_no_release_and_ignore_prereleases(installed, tmp_path):
    root, _, _ = installed
    client = FakeGitHub(tmp_path / "remote")
    client.items = []
    assert check(root, client) == {
        "current_version": "0.5.0",
        "latest_version": None,
        "update_available": False,
    }
    client.items = [
        {"draft": True, "prerelease": False, "tag_name": "v9.0.0"},
        {"draft": False, "prerelease": True, "tag_name": "v9.0.0"},
    ]
    assert candidate(client) is None


@pytest.mark.parametrize("bad", ["mutable", "sha", "digest", "url", "distribution", "asset_changed"])
def test_highest_invalid_no_fallback(installed, tmp_path, bad):
    root, _, _ = installed
    client = FakeGitHub(
        tmp_path / "remote",
        override={"distribution": "skill"} if bad == "distribution" else None,
        asset_change=bad == "asset_changed",
    )
    client.items.insert(
        0, {"draft": False, "prerelease": False, "immutable": True, "tag_name": "v0.4.0", "assets": []}
    )
    top = client.items[-1]
    if bad == "mutable":
        top["immutable"] = False
    elif bad == "sha":
        client.tag_sha = lambda tag: "b" * 40
    elif bad == "digest":
        top["assets"][1]["digest"] = "sha256:" + "0" * 64
    elif bad == "url":
        top["assets"][0]["browser_download_url"] = "https://evil.example/pkg"
    with pytest.raises(LurkerError):
        apply(root, "0.5.1", client) if bad == "asset_changed" else check(root, client)
    assert current(root)[0]["version"] == "0.5.0"


def test_explicit_stable_apply_and_noop(installed, tmp_path):
    root, registry, old_archive = installed
    client = FakeGitHub(tmp_path / "remote")
    assert check(root, client)["update_available"] is True
    with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
        result = apply(root, "0.5.1", client)
    assert result["changed"] and result["current_version"] == "0.5.1"
    assert run_installed(root, ["--version"]).stdout.strip() == "0.5.1"
    with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
        assert not apply(root, "0.5.1", client)["changed"]
    assert (root / "versions/0.5.0").is_dir() and list((root / "backups").glob("*/completed.json"))
    assert not (root / "upgrade-state.json").exists()
    for profile in registry.list():
        db = Database(profile.db_path, profile.profile_id)
        assert db.authors()[0]["works_count"] == 1
        db.close()


class Crash(BaseException):
    pass


@pytest.mark.parametrize(
    "stage",
    [
        "unpacked",
        "backed_up",
        "prepared",
        "migrated:p0001",
        "migrated:p0002",
        "switching",
        "pointer_switched",
        "committed",
        "archived",
    ],
)
def test_every_upgrade_stage_crash_and_recovery(installed, tmp_path, stage):
    root, registry, _ = installed
    archive, _ = make_package(tmp_path / "target", "0.5.1", schema=2)

    def crash(actual):
        if actual == stage:
            raise Crash()

    with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
        with pytest.raises(Crash):
            apply_package(root, archive, fault=crash)
    crossed = stage in {"pointer_switched", "committed", "archived"}
    if crossed:
        # A new fact after commit must survive recovery.
        profile = registry.get("p0001")
        with sqlite3.connect(profile.db_path) as db:
            db.execute("UPDATE works SET title='new-fact'")
    with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
        recover(root)
    assert current(root)[0]["version"] == ("0.5.1" if crossed else "0.5.0")
    for profile in registry.list():
        with sqlite3.connect(profile.db_path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("PRAGMA user_version").fetchone()[0] == (2 if crossed else 1)
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
    if crossed:
        with sqlite3.connect(registry.get("p0001").db_path) as db:
            assert db.execute("SELECT title FROM works").fetchone()[0] == "new-fact"
    assert not (root / "upgrade-state.json").exists()


def test_migration_failure_second_profile_rolls_back_all(installed, tmp_path):
    root, registry, _ = installed
    archive, _ = make_package(tmp_path / "target", "0.5.1", schema=2)

    def fail(stage):
        if stage == "migrated:p0002":
            raise RuntimeError("fault fixture")

    with pytest.raises(LurkerError):
        apply_package(root, archive, fault=fail)
    assert current(root)[0]["version"] == "0.5.0"
    for profile in registry.list():
        with sqlite3.connect(profile.db_path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_bootstrap_recovers_authorized_plan_before_business(installed, tmp_path):
    root, _, _ = installed
    archive, _ = make_package(tmp_path / "target", "0.5.1", schema=2)

    def crash(stage):
        if stage == "migrated:p0001":
            raise Crash()

    with pytest.raises(Crash):
        apply_package(root, archive, fault=crash)
    result = run_installed(root, ["--profile", "p0001", "list"])
    assert result.returncode == 0 and result.stderr == ""
    assert current(root)[0]["version"] == "0.5.0" and not (root / "upgrade-state.json").exists()


def test_corrupt_plan_and_backups_keep_maintenance(installed, tmp_path):
    root, registry, _ = installed
    atomic_json(root / "upgrade-state.json", {"stage": "guess"})
    with pytest.raises(LurkerError):
        recover(root)
    result = run_installed(root, ["--profile", "p0001", "check"])
    assert result.returncode == 1 and (root / "upgrade-state.json").exists()


def test_live_business_shared_lock_blocks_upgrade(installed):
    root, _, _ = installed
    with lock(root / "install.lock", shared=True):
        result = run_installed(root, ["upgrade", "apply", "--version", "0.5.1"])
        assert result.returncode == 4
        assert json.loads(result.stdout.splitlines()[0])["error"]["code"] == "MAINTENANCE_BUSY"


@pytest.mark.parametrize("stage", ["prepared", "pointer_switched"])
def test_empty_registry_recovery(tmp_path, installer, stage):
    archive, _ = make_package(tmp_path / "old")
    root = tmp_path / "install"
    installer(archive, root, tmp_path / "data")
    target, _ = make_package(tmp_path / "new", "0.5.1")

    def fault(actual):
        if actual == stage:
            raise Crash()

    with pytest.raises(Crash):
        apply_package(root, target, fault=fault)
    recover(root)
    assert current(root)[0]["version"] == ("0.5.1" if stage == "pointer_switched" else "0.5.0")
    assert not (root / "upgrade-state.json").exists()


def test_bad_backup_does_not_restore_any_profile(installed, tmp_path):
    root, registry, _ = installed
    target, _ = make_package(tmp_path / "new", "0.5.1", schema=2)

    def fault(stage):
        if stage == "migrated:p0001":
            raise Crash()

    with pytest.raises(Crash):
        apply_package(root, target, fault=fault)
    plan = json.loads((root / "upgrade-state.json").read_text())
    (root / "backups" / plan["id"] / "p0002.sqlite").write_bytes(b"broken")
    before = [digest(p.db_path) for p in registry.list()]
    with pytest.raises(LurkerError):
        recover(root)
    assert before == [digest(p.db_path) for p in registry.list()]
    assert (root / "upgrade-state.json").exists()


def test_lost_registry_rejected_without_scanning(installed, tmp_path):
    root, registry, _ = installed
    registry.path.unlink()
    target, _ = make_package(tmp_path / "new", "0.5.1")
    with pytest.raises(LurkerError):
        apply_package(root, target)
    assert current(root)[0]["version"] == "0.5.0"


def test_candidate_version_mismatch_blocks_install(tmp_path, installer):
    archive, _ = make_package(tmp_path / "bad", overrides={"version": "0.5.1"})
    with pytest.raises(LurkerError):
        installer(archive, tmp_path / "install", tmp_path / "data")
    assert not (tmp_path / "install/current.json").exists()


def test_additive_migration_remains_writable_after_switch(installed, tmp_path):
    root, registry, _ = installed
    archive, _ = make_package(tmp_path / "new", "0.5.1", schema=2)
    apply_package(root, archive)
    profile = registry.get("p0001")
    # The candidate's writer must explicitly name columns when the schema adds columns.
    script = (
        "import sys;sys.path.insert(0,sys.argv[1]);from social_lurker.db import Database;"
        "from social_lurker.adapters.base import Author,Work;d=Database(sys.argv[2],'p0001');"
        "d.upsert_page(Author('douyin','author-a','甲'),[Work('douyin','after-upgrade','author-a')]);d.close()"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(root / "versions/0.5.1/src"), str(profile.db_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    with sqlite3.connect(profile.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 2
