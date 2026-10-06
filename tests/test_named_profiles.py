import io
import json

import pytest
from test_upgrade import Crash, make_package

from social_lurker.cli import main
from social_lurker.db import Database
from social_lurker.errors import LurkerError
from social_lurker.locks import lock
from social_lurker.profiles import FileRegistry
from social_lurker.upgrade import apply_package, current, recover
from social_lurker.util import atomic_json


@pytest.mark.parametrize(
    "name",
    [
        "中文 空间",
        "grok:uuid/agent",
        "../outside",
        "/tmp/absolute",
        "--help",
        "a\\b",
        "a\nb",
        " ",
        "p0000",
        "0",
        "😀",
    ],
)
def test_opaque_name_roundtrip_and_safe_paths(tmp_path, name):
    registry = FileRegistry(tmp_path / "data")
    p = registry.create(name)
    assert p.profile_id == name and p.label == name
    assert registry.create(name) == p
    assert registry.get(name) == p
    assert p.db_path.parent.parent == registry.root / "profiles"
    assert p.backup_name.startswith("id-") and "/" not in p.backup_name
    db = Database(p.db_path, name)
    db.close()
    assert len(registry.list()) == 1
    assert json.loads(registry.path.read_text())["schema_version"] == 2
    assert "next_id" not in json.loads(registry.path.read_text())


def test_exact_names_are_distinct(tmp_path):
    registry = FileRegistry(tmp_path)
    names = ["Agent", "agent", " agent", "é", "e\u0301"]
    profiles = [registry.create(name) for name in names]
    assert len({p.db_path for p in profiles}) == len(names)
    assert [p.profile_id for p in registry.list()] == names


@pytest.mark.parametrize("name", ["", "x" * 1025, "\ud800", None, 1])
def test_invalid_names_do_not_create_registry(tmp_path, name):
    registry = FileRegistry(tmp_path)
    with pytest.raises(LurkerError) as error:
        registry.create(name)
    assert error.value.code == "INPUT_INVALID"
    assert not registry.path.exists()


def test_old_registry_preserved_until_explicit_create(tmp_path):
    registry = FileRegistry(tmp_path)
    original = {
        "schema_version": 1,
        "next_id": 2,
        "profiles": [{"id": "p0001", "label": "旧空间", "db": "profiles/p0001/state.sqlite"}],
    }
    atomic_json(registry.path, original)
    before = registry.path.read_bytes()
    p = registry.get("p0001")
    db = Database(p.db_path, p.profile_id)
    db.close()
    assert p.backup_name == "p0001.sqlite"
    assert registry.create("p0001") == p
    assert registry.path.read_bytes() == before
    registry.create("自媒体")
    assert registry.get("p0001") == p
    assert registry.load()["schema_version"] == 2
    assert registry.load()["profiles"][0] == original["profiles"][0]


def test_registry_rejects_path_tampering_and_symlink(tmp_path):
    registry = FileRegistry(tmp_path)
    p = registry.create("../outside")
    value = registry.load()
    value["profiles"][0]["db"] = "../outside/state.sqlite"
    atomic_json(registry.path, value)
    with pytest.raises(LurkerError):
        registry.list()
    value["profiles"][0]["db"] = p.db_path.relative_to(tmp_path).as_posix()
    atomic_json(registry.path, value)
    (tmp_path / "profiles").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    p.db_path.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LurkerError):
        registry.get("../outside")


def test_cli_requires_name_and_keeps_exact_id(tmp_path):
    args = ["--install-root", str(tmp_path / "install"), "--data-root", str(tmp_path / "data")]
    stream = io.StringIO()
    assert main(args + ["profile", "create"], stdout=stream) == 2
    assert not (tmp_path / "data/profiles.json").exists()
    stream = io.StringIO()
    assert main(args + ["--profile=../我的 agent", "profile", "create"], stdout=stream) == 0
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert records[0]["profile_id"] == "../我的 agent"
    assert records[0]["payload"]["profile_id"] == "../我的 agent"
    assert main(args + ["--profile=../我的 agent", "list"], stdout=io.StringIO()) == 0
    assert main(args + ["list"], stdout=io.StringIO()) == 2
    assert main(args + ["--profile=other", "list"], stdout=io.StringIO()) == 2
    assert main(args + ["profile", "list"], stdout=io.StringIO()) == 0
    assert len(FileRegistry(tmp_path / "data").list()) == 1


@pytest.mark.parametrize("stage", ["prepared", "migrated:../../escape", "pointer_switched"])
def test_upgrade_safe_backup_and_recovery_for_arbitrary_ids(tmp_path, installer, stage):
    root, data = tmp_path / "install", tmp_path / "data"
    archive, _ = make_package(tmp_path / "old", version="0.5.0")
    installer(archive, root, data)
    p = FileRegistry(data).create("../../escape")
    db = Database(p.db_path, p.profile_id)
    db.close()
    target, _ = make_package(tmp_path / "new", version="0.5.1", schema=2)

    def crash(at):
        if at == stage:
            raise Crash()

    with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
        with pytest.raises(Crash):
            apply_package(root, target, fault=crash)
        recover(root)
    crossed = stage == "pointer_switched"
    assert current(root)[0]["version"] == ("0.5.1" if crossed else "0.5.0")
    backup = next((root / "backups").glob("*/completed.json")).parent
    assert (backup / p.backup_name).is_file()
    assert not (root / "escape.sqlite").exists()
    db = Database(p.db_path, p.profile_id, create=False, supported_schema=2 if crossed else 1)
    db.close()
