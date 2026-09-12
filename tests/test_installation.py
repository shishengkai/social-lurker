import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from social_lurker.config import Instance
from social_lurker.setup import configure_tool_path, status
from social_lurker.util import token, write_json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "install.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture
def snapshot(monkeypatch):
    files = {
        "app/social_lurker/__init__.py": b'__version__ = "0.2.0"',
        "requirements-runtime.txt": b"lock",
        "launcher.py": b"launcher",
    }
    manifest = {
        "project": "social-lurker",
        "version": "0.2.0-dev",
        "channel": "development",
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    }
    calls = []

    def make_venv(python, target, requirements):
        calls.append(target)
        (target / "bin").mkdir(parents=True)
        (target / "bin/python").write_text("placeholder")

    monkeypatch.setattr(bootstrap, "source_contents", lambda *args: (files, manifest))
    monkeypatch.setattr(bootstrap, "make_venv", make_venv)
    return files, manifest, calls


def test_bootstrap_parses_before_python_312_is_installed():
    ast.parse((ROOT / "install.py").read_text(), feature_version=(3, 9))


def test_stable_worker_keeps_actionable_public_error_without_raw_logs(monkeypatch):
    import subprocess

    public = {"error": {"code": "RELEASE_UNAVAILABLE", "message": "尚无正式稳定 Release"}}
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, json.dumps(public), "raw installer diagnostic"),
    )
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.child(["worker"], error_json=True)
    assert error.value.code == "RELEASE_UNAVAILABLE"
    assert error.value.message == "尚无正式稳定 Release"
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.child(["third-party-installer"])
    assert error.value.code == "INSTALL_STEP_FAILED"
    assert "raw installer" not in error.value.message


def test_stable_bot_identity_and_missing_binding():
    first = bootstrap.instance_id(None, "platform-one")
    assert first == bootstrap.instance_id(None, "platform-one")
    assert first != bootstrap.instance_id(None, "platform-two")
    assert bootstrap.instance_id(first, "platform-one") == first
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.instance_id(None, None)
    assert error.value.code == "INSTANCE_BINDING_REQUIRED"
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.instance_id("not-a-uuid", None)
    assert error.value.code == "INSTANCE_BINDING_REQUIRED"


def test_install_failure_identifies_step_without_command_input_or_raw_logs(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], -9, "private stdout", "private stderr"),
    )
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.child(["tool", "private argument"], input="private stdin", step="准备运行依赖")
    assert error.value.code == "INSTALL_STEP_FAILED"
    assert "准备运行依赖" in error.value.message and "-9" in error.value.message
    assert "private" not in error.value.message


def test_legacy_package_without_manifest_inventory_is_preserved(tmp_path):
    (tmp_path / "old-data").write_text("retain")
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.verify_package(tmp_path, {"version": "0.1.0-dev"})
    assert error.value.code == "LEGACY_INSTALL"
    assert (tmp_path / "old-data").read_text() == "retain"


def test_preview_reuses_verified_package_for_second_bot(tmp_path, snapshot):
    _, _, calls = snapshot
    first = bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    second = bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    assert first == second and len(calls) == 1
    a, b = Instance(tmp_path, token()), Instance(tmp_path, token())
    a.initialize(binding_confirmed=True, runtime_version="0.2.0-dev")
    a.set_credentials({"FAL_KEY": "local-test-value"})
    b.initialize(binding_confirmed=True, runtime_version="0.2.0-dev")
    assert not b.credentials()["FAL_KEY"]
    assert a.credentials()["FAL_KEY"] == "local-test-value"


def test_failure_then_repeat_rebuilds_only_unpublished_stage(tmp_path, snapshot, monkeypatch):
    _, _, calls = snapshot
    good = bootstrap.make_venv
    existing = tmp_path / "bots/retained/.env"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep")

    def fail(*args):
        raise bootstrap.InstallError("INSTALL_STEP_FAILED", "interrupted")

    monkeypatch.setattr(bootstrap, "make_venv", fail)
    with pytest.raises(bootstrap.InstallError):
        bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    assert not (tmp_path / "runtime/releases/0.2.0-dev").exists()
    # A kill can leave staging behind; the same reserved staging is safely retried.
    stage = tmp_path / "runtime/releases/0.2.0-dev.installing"
    stage.mkdir()
    (stage / "partial").write_text("incomplete")
    monkeypatch.setattr(bootstrap, "make_venv", good)
    folder = bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    assert folder.is_dir() and len(calls) == 1
    assert not stage.exists() and existing.read_text() == "keep"


def test_same_version_changed_source_or_installed_bytes_never_overwritten(tmp_path, snapshot):
    files, manifest, _ = snapshot
    folder = bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    files["launcher.py"] = b"new launcher"
    manifest["files"]["launcher.py"] = hashlib.sha256(files["launcher.py"]).hexdigest()
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    assert error.value.code == "VERSION_CONTENT_CONFLICT"
    assert (folder / "launcher.py").read_bytes() == b"launcher"
    saved = json.loads((folder / "manifest.json").read_text())
    (folder / "launcher.py").write_bytes(b"tampered")
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.verify_package(folder, saved)
    assert error.value.code == "PACKAGE_CHANGED"


def test_repeated_install_preserves_config_database_and_bound_version(tmp_path, snapshot, monkeypatch):
    _, _, calls = snapshot
    folder = bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    instance = Instance(tmp_path, token())
    settings = instance.initialize(binding_confirmed=True, runtime_version="0.2.0-dev")
    settings["check_interval_minutes"] = 45
    write_json(instance.settings_path, settings)
    instance.set_credentials({"FAL_KEY": "preserve-test-value"})
    instance.db_path.write_bytes(b"existing database")
    paths = [instance.settings_path, instance.path / ".env", instance.db_path]
    before = [path.read_bytes() for path in paths]
    monkeypatch.setattr(bootstrap, "environment", lambda *a, **k: {"python": sys.executable})
    monkeypatch.setattr(
        bootstrap, "preview_package", lambda *a, **k: pytest.fail("must not reinstall or upgrade")
    )

    def child(args, **kwargs):
        assert args[0] == folder / ".venv/bin/python"
        return json.dumps({"error": None, "result": {"next_actions": []}})

    monkeypatch.setattr(bootstrap, "child", child)
    result = bootstrap.install(
        SimpleNamespace(
            root=tmp_path,
            instance=instance.id,
            platform_bot_id=None,
            system_only=True,
            channel="stable",
            allow_working_tree=False,
        )
    )
    assert result["resumed"] and result["runtime_version"] == "0.2.0-dev"
    assert before == [path.read_bytes() for path in paths] and len(calls) == 1


def test_unhealthy_published_tool_environment_is_not_replaced(tmp_path, monkeypatch):
    record = tmp_path / "runtime/tools/environment.json"
    bootstrap.save(record, {"python": "/missing/python", "bin_dirs": ["/missing"]})
    monkeypatch.setattr(bootstrap, "probe", lambda *args: None)
    monkeypatch.setattr(bootstrap, "download_mamba", lambda *args: pytest.fail("must preserve shared tools"))
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.environment(tmp_path)
    assert error.value.code == "ENVIRONMENT_REPAIR_REQUIRED"


def test_first_stable_install_is_not_reported_as_resume(tmp_path, snapshot, monkeypatch):
    preview = bootstrap.preview_package(tmp_path, ROOT, sys.executable)
    folder = preview.with_name("0.2.0")
    preview.rename(folder)
    ident = token()
    (tmp_path / "runtime/tools").mkdir()
    monkeypatch.setattr(bootstrap, "environment", lambda *a, **k: {"python": sys.executable})

    def child(args, **kwargs):
        if any(str(a).endswith("tools/install_stable.py") for a in args):
            Instance(tmp_path, ident).initialize(binding_confirmed=True, runtime_version="0.2.0")
        return json.dumps({"error": None, "result": {"next_actions": []}})

    monkeypatch.setattr(bootstrap, "child", child)
    result = bootstrap.install(
        SimpleNamespace(
            root=tmp_path,
            instance=ident,
            platform_bot_id=None,
            system_only=True,
            channel="stable",
            allow_working_tree=False,
        )
    )
    assert not result["resumed"] and result["runtime_version"] == "0.2.0"


def test_reject_install_symlinks_and_lock_concurrency(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "runtime").symlink_to(outside, target_is_directory=True)
    with pytest.raises(bootstrap.InstallError):
        bootstrap.safe(tmp_path, "runtime/releases")
    with bootstrap.lock(tmp_path / "test.lock"):
        with pytest.raises(bootstrap.InstallError) as error:
            with bootstrap.lock(tmp_path / "test.lock"):
                pytest.fail("lock must exclude another installer")
        assert error.value.code == "BUSY"


def test_scheduled_cli_restores_explicit_tool_paths(tmp_path, monkeypatch):
    bootstrap.save(tmp_path / "runtime/tools/environment.json", {"bin_dirs": ["/project/tools/bin"]})
    monkeypatch.setenv("PATH", "/usr/bin")
    configure_tool_path(tmp_path)
    configure_tool_path(tmp_path)
    import os

    assert os.environ["PATH"] == "/project/tools/bin:/usr/bin"


def test_onboarding_reports_only_missing_steps_and_no_credential_values(env, monkeypatch):
    instance, store, settings = env
    settings["delivery"]["verified"] = False
    checks = {
        "python": True,
        "ffmpeg": True,
        "ffprobe": True,
        "node": True,
        "credentials": False,
        "database": True,
        "delivery_verified": False,
    }
    monkeypatch.setattr(
        "social_lurker.setup.doctor", lambda *a: {"checks": checks.copy(), "routine_interval_synced": False}
    )
    first = status(instance, settings, store)
    assert [a["action"] for a in first["next_actions"]] == [
        "credentials",
        "native_delivery_test",
        "native_routine",
    ]
    instance.set_credentials({"TIKHUB_API_KEY": "private-test-value", "FAL_KEY": "private-test-value"})
    settings["delivery"]["verified"] = True
    second = status(instance, settings, store)
    assert [a["action"] for a in second["next_actions"]] == ["native_routine"]
    assert "private-test-value" not in json.dumps(second)
    assert second["routine"]["name"] == "social-lurker:" + instance.id
    assert instance.id in second["binding_instructions"]
    registrations = second["skill_registrations"]
    assert len(registrations) == 3
    assert instance.id not in json.dumps(registrations)
    assert settings["runtime_version"] not in json.dumps(registrations)


def test_download_digest_failure_never_extracts_binary(tmp_path, monkeypatch):
    import io

    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"wrong archive"))
    with pytest.raises(bootstrap.InstallError) as error:
        bootstrap.download_mamba(tmp_path / "micromamba", "x86_64")
    assert error.value.code == "DIGEST_MISMATCH"
    assert not (tmp_path / "micromamba").exists()
