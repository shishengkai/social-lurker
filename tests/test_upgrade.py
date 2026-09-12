import copy
import hashlib
import io
import json
import tarfile

import httpx
import pytest

from social_lurker.errors import LurkerError
from social_lurker.upgrade import discover, is_newer, recover_upgrade, unpack_verified, verify_manifest
from social_lurker.util import write_json

FILES = {
    "app/social_lurker/cli.py": b"cli",
    "app/social_lurker/schema.sql": b"schema",
    "requirements-runtime.txt": b"lock",
    "skills/social-lurker/SKILL.md": b"skill",
    "skills/social-lurker-setup/SKILL.md": b"setup skill",
    "skills/social-lurker-upgrader/SKILL.md": b"upgrader skill",
    "skills/social-lurker-upgrader/references/star.md": b"shared star instructions",
    "app/social_lurker/setup.py": b"setup",
    "vendor/wechat-decrypt/decrypt.cjs": b"decrypt",
    "launcher.py": b"launcher",
}


def manifest():
    return {
        "project": "social-lurker",
        "authority": "github.com/shishengkai/social-lurker",
        "channel": "stable",
        "version": "0.2.0",
        "source_commit": "a" * 40,
        "released_at": "2026-09-12T00:00:00Z",
        "summary": "验证",
        "launcher_protocol": {"min": 1, "max": 1},
        "schema_version": 1,
        "migrate_from": [1],
        "python": ">=3.12,<4",
        "platforms": [["linux", "x86_64"]],
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in FILES.items()},
    }


def archive(tmp_path, *, bad=None):
    m = manifest()
    target = tmp_path / "package.tar.gz"
    contents = FILES | {"manifest.json": json.dumps(m).encode()}
    with tarfile.open(target, "w:gz") as tar:
        for name, data in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if bad == "symlink" and name.endswith("cli.py"):
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                info.size = 0
                tar.addfile(info)
            else:
                tar.addfile(info, io.BytesIO(data))
        if bad == "traversal":
            info = tarfile.TarInfo("../escape")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    return target, {"manifest": m, "archive_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def test_package_requires_exact_digest_inventory_and_no_links(tmp_path):
    path, descriptor = archive(tmp_path)
    unpack_verified(path, tmp_path / "good", descriptor)
    assert (tmp_path / "good/app/social_lurker/cli.py").read_bytes() == b"cli"
    bad = copy.deepcopy(descriptor)
    bad["archive_sha256"] = "0" * 64
    with pytest.raises(LurkerError):
        unpack_verified(path, tmp_path / "bad", bad)
    for kind in ("symlink", "traversal"):
        path, descriptor = archive(tmp_path, bad=kind)
        with pytest.raises(LurkerError):
            unpack_verified(path, tmp_path / kind, descriptor)
        assert not (tmp_path / "escape").exists()


def test_manifest_fixed_authority_schema_and_launcher():
    for patch in (
        {"authority": "github.com/attacker/repo"},
        {"channel": "nightly"},
        {"schema_version": 9},
        {"launcher_protocol": {"min": 1, "max": 2}},
    ):
        with pytest.raises(LurkerError):
            verify_manifest(manifest() | patch)
    assert is_newer("0.1.0", "0.1.0-dev")
    assert not is_newer("0.1.0", "0.1.0")


def test_new_release_requires_all_three_skills():
    for name in ("social-lurker-setup", "social-lurker-upgrader"):
        incomplete = manifest()
        incomplete["files"].pop(f"skills/{name}/SKILL.md")
        with pytest.raises(LurkerError) as error:
            verify_manifest(incomplete)
        assert error.value.code == "RELEASE_INVALID"


def test_invalid_highest_release_never_falls_back():
    calls = []

    def response(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {"tag_name": "v0.2.0", "immutable": True},
                {"tag_name": "v0.3.0", "immutable": False},
                {"tag_name": "v9.0.0", "prerelease": True, "immutable": True},
            ],
        )

    with pytest.raises(LurkerError) as error:
        discover(httpx.Client(transport=httpx.MockTransport(response)))
    assert error.value.code == "RELEASE_INVALID"
    assert len(calls) == 1


def test_discovery_pins_tag_and_source_commit():
    m = manifest()

    def response(request):
        path = request.url.path
        if path.endswith("/releases"):
            data = [
                {
                    "tag_name": "v0.2.0",
                    "immutable": True,
                    "assets": [
                        {
                            "name": "social-lurker-0.2.0.tar.gz",
                            "browser_download_url": "https://github.com/shishengkai/social-lurker/releases/download/v0.2.0/social-lurker-0.2.0.tar.gz",
                        }
                    ],
                }
            ]
        elif "/git/ref/" in path:
            data = {"object": {"type": "commit", "sha": "b" * 40}}
        elif path.endswith("/release-manifest.json"):
            assert "b" * 40 in path
            data = {"manifest": m, "archive_sha256": "c" * 64}
        elif "/compare/" in path:
            data = {"status": "ahead", "merge_base_commit": {"sha": "a" * 40}}
        else:
            pytest.fail(path)
        return httpx.Response(200, json=data)

    result = discover(httpx.Client(transport=httpx.MockTransport(response)))
    assert result["release_commit"] == "b" * 40
    assert result["manifest"]["source_commit"] == "a" * 40


def test_migration_recovery_restores_before_business_reopens(env):
    import sqlite3

    from conftest import author

    instance, store, settings = env
    store.add_account(author(), "add")
    with sqlite3.connect(instance.maintenance / "upgrade.sqlite3") as target:
        store.conn.backup(target)
    write_json(instance.maintenance / "upgrade-settings.json", settings)
    write_json(instance.maintenance / "upgrade.json", {"stage": "migrating"})
    store.execute("UPDATE accounts SET display_name='half migration'")
    assert recover_upgrade(instance, store)["database_restored"]
    assert store.account(1)["display_name"] == "测试博主"
    assert not (instance.maintenance / "upgrade.json").exists()


def test_committed_upgrade_never_restores_old_database(env):
    from conftest import author

    instance, store, _ = env
    store.add_account(author(), "add")
    write_json(instance.maintenance / "upgrade.json", {"stage": "committed"})
    store.execute("UPDATE accounts SET display_name='new writes'")
    assert not recover_upgrade(instance, store)["database_restored"]
    assert store.account(1)["display_name"] == "new writes"
