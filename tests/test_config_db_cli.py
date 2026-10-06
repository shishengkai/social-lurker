import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import work

from social_lurker.adapters.base import Page
from social_lurker.cli import main
from social_lurker.config import FileSecrets
from social_lurker.db import Database
from social_lurker.errors import LurkerError
from social_lurker.locks import lock
from social_lurker.profiles import FileRegistry


def test_file_priority_unconfigured_env_empty_invalid(tmp_path):
    path = tmp_path / "credentials.json"
    s = FileSecrets(path, {"TIKHUB_API_KEY": "env-fixture"})
    assert s.get_key() == "env-fixture"
    path.write_text("{}")
    assert s.get_key() == "env-fixture"
    s.set_key("file-fixture")
    assert s.get_key() == "file-fixture"
    assert path.stat().st_mode & 0o777 == 0o600
    assert s.status() == {"configured": True, "source": "file"}
    for content in ('{"tikhub_api_key":""}', '{"tikhub_api_key":null}', "not json", "[]"):
        path.write_text(content)
        with pytest.raises(LurkerError) as error:
            s.get_key()
        assert error.value.code == "CONFIG_INVALID"
    path.unlink()
    with pytest.raises(LurkerError) as error:
        FileSecrets(path, {}).get_key()
    assert error.value.code == "CREDENTIAL_MISSING"


def test_registry_caller_names_no_default_ids_are_not_paths(tmp_path):
    registry = FileRegistry(tmp_path)
    one = registry.create("../中文 空间 --help")
    two = registry.create("同名")
    assert (one.profile_id, two.profile_id) == ("../中文 空间 --help", "同名")
    assert one.db_path.parent.parent == tmp_path / "profiles"
    assert "中文" not in str(one.db_path)
    assert [p.profile_id for p in registry.list()] == ["../中文 空间 --help", "同名"]
    with pytest.raises(LurkerError):
        registry.get("p0000")
    for value in ('{"profiles":[]}', "null", '{"schema_version":1,"next_id":1,"profiles":[]}'):
        registry.path.write_text(value)
        if value != '{"schema_version":1,"next_id":1,"profiles":[]}':
            with pytest.raises(LurkerError):
                registry.list()
    registry.path.unlink()
    (tmp_path / "profiles").mkdir()
    with pytest.raises(LurkerError):
        registry.create("lost")


@pytest.mark.parametrize("variant", ["old", "empty", "higher", "wrong_profile", "missing_meta"])
def test_refuse_wrong_database_without_rewriting(env, variant):
    _, profile, _, db, _, _, _, _, _ = env
    db.close()
    path = profile.db_path
    if variant in {"old", "empty"}:
        path.unlink()
        if variant == "old":
            with sqlite3.connect(path) as c:
                c.execute("CREATE TABLE runtime (id)")
        else:
            path.touch()
    else:
        with sqlite3.connect(path) as c:
            if variant == "higher":
                c.execute("PRAGMA user_version=2")
            elif variant == "wrong_profile":
                c.execute("UPDATE meta SET value='other'")
            else:
                c.execute("DELETE FROM meta")
    before = path.read_bytes()
    with pytest.raises((LurkerError, TypeError)):
        Database(path, profile.profile_id)
    assert path.read_bytes() == before


def test_same_profile_busy_other_profile_and_install_shared(env, tmp_path):
    registry, profile, *_ = env
    p2 = registry.create("乙")
    with lock(profile.lock_path):
        with pytest.raises(LurkerError) as caught:
            with lock(profile.lock_path):
                pass
        assert caught.value.code == "PROFILE_BUSY"
        with lock(p2.lock_path):
            pass
    with lock(tmp_path / "install.lock", shared=True):
        with lock(tmp_path / "install.lock", shared=True):
            pass
        with pytest.raises(LurkerError) as caught:
            with lock(tmp_path / "install.lock", code="MAINTENANCE_BUSY"):
                pass
        assert caught.value.code == "MAINTENANCE_BUSY"


def invoke(env, tmp_path, args, fmt="jsonl"):
    registry, _, secrets, _, _, _, _, adapter, _ = env
    out = io.StringIO()
    code = main(
        ["--install-root", str(tmp_path / "install"), "--format", fmt] + args,
        registry=registry,
        secrets=secrets,
        adapters={"douyin": adapter},
        stdout=out,
    )
    return code, out.getvalue()


def test_cli_requires_explicit_profile_and_never_echoes_argv(env, tmp_path):
    for args in (["check"], ["--profile", "p9999", "list"], ["--unknown-secret=fixture-private"]):
        code, out = invoke(env, tmp_path, args)
        assert code == 2 and "fixture-private" not in out
        data = [json.loads(line) for line in out.splitlines()]
        assert [r["type"] for r in data] == ["error", "complete"]
        assert data[-1]["status"] == "error"
    code, out = invoke(env, tmp_path, ["--profile", "p0001", "upgrade", "check"], "json")
    assert code == 2 and json.loads(out)["completion"]["status"] == "error"


def test_json_and_jsonl_same_order_and_completion(env, tmp_path):
    _, profile, _, _, _, _, _, adapter, _ = env
    adapter.pages = {None: Page([work("old")])}
    assert (
        invoke(env, tmp_path, ["--profile", profile.profile_id, "add", "https://www.douyin.com/user/a"])[0]
        == 0
    )
    for fmt, new in (("jsonl", "A"), ("json", "B")):
        adapter.pages = {None: Page([work(new), work("old")])}
        code, out = invoke(env, tmp_path, ["--profile", profile.profile_id, "check"], fmt)
        assert code == 0
        if fmt == "json":
            parsed = json.loads(out)
            rows, completion = parsed["records"], parsed["completion"]
        else:
            parsed = [json.loads(line) for line in out.splitlines()]
            rows, completion = parsed[:-1], parsed[-1]
        assert [r["type"] for r in rows] == ["work"]
        assert rows[0]["work"]["work_id"] == new
        assert completion["summary"]["new_works"] == 1 and completion["scan_complete"] is True


def test_no_authors_no_key_is_ok(env, tmp_path):
    env[2].environ.clear()
    code, out = invoke(env, tmp_path, ["--profile", "p0001", "check"])
    assert code == 0 and json.loads(out)["summary"]["requests"] == 0
    assert env[7].requests == []


def test_cli_busy_code_and_list_snapshot(env, tmp_path):
    with lock(env[1].lock_path):
        code, _ = invoke(env, tmp_path, ["--profile", "p0001", "check"])
        assert code == 4
        assert invoke(env, tmp_path, ["--profile", "p0001", "list"])[0] == 0


def test_cli_parameter_error_json_and_help_in_subprocess(tmp_path):
    import os

    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    for args, expected in ((["--format=json", "invalid"], 2), (["--help"], 0), (["--version"], 0)):
        result = subprocess.run(
            [sys.executable, "-m", "social_lurker", *args], env=environment, capture_output=True, text=True
        )
        assert result.returncode == expected and result.stderr == ""
        if expected:
            assert json.loads(result.stdout)["completion"]["status"] == "error"


def test_native_process_profile_lock_and_independent_other_profile(env, tmp_path):
    import os

    registry, profile, *_ = env
    p2 = registry.create("乙")
    other = Database(p2.db_path, p2.profile_id)
    other.close()
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    common = [
        sys.executable,
        "-B",
        "-m",
        "social_lurker",
        "--install-root",
        str(tmp_path / "install"),
        "--data-root",
        str(registry.root),
    ]
    with lock(profile.lock_path):
        for pid, expected in ((profile.profile_id, 4), (p2.profile_id, 0)):
            result = subprocess.run(
                [*common, "--profile", pid, "check"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert result.returncode == expected and result.stderr == ""
            assert json.loads(result.stdout.splitlines()[-1])["summary"]["requests"] == 0
