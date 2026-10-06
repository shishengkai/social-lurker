"""Offline install/upgrade smoke in disposable directories; no provider or host operations."""

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from install import install
from social_lurker import __version__
from social_lurker.db import Database
from social_lurker.locks import lock
from social_lurker.profiles import FileRegistry
from social_lurker.upgrade import apply_package, check, current
from tools.build_release import build


def fixture_upgrade(archive, path):
    files = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            files[member.name] = tar.extractfile(member).read()
    manifest = json.loads(files.pop("manifest.json"))
    manifest.update(version="0.5.2", schema_max=2, schema_target=2, migrations={"1": "migrations/1.sql"})
    files["src/social_lurker/__init__.py"] = files["src/social_lurker/__init__.py"].replace(
        f'"{__version__}"'.encode(), b'"0.5.2"'
    )
    files["src/social_lurker/db.py"] = files["src/social_lurker/db.py"].replace(
        b"SCHEMA_VERSION = 1", b"SCHEMA_VERSION = 2"
    )
    files["migrations/1.sql"] = b"ALTER TABLE works ADD COLUMN smoke_fixture TEXT;"
    manifest["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files["manifest.json"] = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class NoReleases:
    def releases(self):
        return []


def smoke():
    with tempfile.TemporaryDirectory(prefix="social-lurker-smoke-") as tmp:
        root = Path(tmp)
        # Spaces deliberately exercise the installed executable's quoting and Python selection.
        install_root, data_root = root / "新安装 with spaces", root / "新数据 with spaces"
        archive = build(ROOT, root / "packages", development=True)
        install(archive, install_root, data_root)
        entry = install_root / "bin/social-lurker"
        credentials = root / "credentials.json"

        def run(args, code=0, stdin=None):
            result = subprocess.run(
                [str(entry), "--credentials-file", str(credentials), *args],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == code, result.stdout
            assert result.stderr == "" and "offline-fixture" not in result.stdout
            return result.stdout

        assert run(["--version"]).strip() == __version__
        assert json.loads(run(["profile", "list"]).splitlines()[0])["payload"]["profiles"] == []
        run(["check"], code=2)
        run(["profile", "create", "--label", "娱乐"])
        run(["profile", "create", "--label", "知识"])
        run(["config", "set-key", "--stdin"], stdin="offline-fixture\n")
        assert credentials.stat().st_mode & 0o777 == 0o600
        for pid in ("p0001", "p0002"):
            assert (
                json.loads(run(["--profile", pid, "--format", "json", "check"]))["completion"]["summary"][
                    "requests"
                ]
                == 0
            )
        assert check(install_root, NoReleases())["latest_version"] is None
        target = fixture_upgrade(archive, root / "synthetic-upgrade.tar.gz")
        with lock(install_root / "install.lock", code="MAINTENANCE_BUSY"):
            result = apply_package(install_root, target)
        assert result["changed"] and len(result["migration_results"]) == 2
        assert current(install_root)[0]["version"] == "0.5.2"
        assert run(["--version"]).strip() == "0.5.2"
        run(["--profile", "p0001", "list"])
        for profile in FileRegistry(data_root).list():
            db = Database(profile.db_path, profile.profile_id, create=False, supported_schema=2)
            assert db.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            db.close()
        assert (install_root / "versions" / __version__).is_dir()
        assert list((install_root / "backups").glob("*/completed.json"))
        return {
            "install": "passed",
            "explicit_profiles": "passed",
            "credentials_stdin": "passed",
            "no_release_discovery": "passed",
            "synthetic_upgrade_and_migration": "passed",
            "provider_requests": 0,
            "directory": "disposable",
            "released_software": False,
        }


if __name__ == "__main__":
    print(json.dumps(smoke(), ensure_ascii=False))
