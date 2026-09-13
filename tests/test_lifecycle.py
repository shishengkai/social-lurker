import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ingest, publication
from test_delivery import receipt
from test_discovery import rows

from social_lurker.delivery import Delivery
from social_lurker.errors import LurkerError
from social_lurker.lifecycle import Lifecycle
from social_lurker.operations import install
from social_lurker.releases import prepare
from social_lurker.util import atomic_json, digest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("builder", ROOT / "tools/build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Crash(BaseException):
    pass


@pytest.fixture
def installed(instance):
    install(instance, source=ROOT)
    settings = instance.load()
    settings["host"]["routine_id"] = None
    atomic_json(instance.path("settings.json"), settings)
    return instance


@pytest.fixture
def package(tmp_path):
    source = tmp_path / "target-source"
    source.mkdir()
    shutil.copytree(ROOT / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "skills", source / "skills")
    (source / "tools").mkdir()
    shutil.copyfile(ROOT / "tools/launcher.py", source / "tools/launcher.py")
    shutil.copyfile(ROOT / "LICENSE", source / "LICENSE")
    init = source / "src/social_lurker/__init__.py"
    init.write_text(init.read_text().replace("0.3.5", "0.3.6"))
    output = tmp_path / "release"
    descriptor = builder.build(source, output, version="0.3.6", commit="a" * 40)
    raw = (output / "social-lurker-0.3.6.tar.gz").read_bytes()
    descriptor["archive_url"] = (
        "https://github.com/shishengkai/social-lurker/releases/download/v0.3.6/social-lurker-0.3.6.tar.gz"
    )
    return descriptor, raw


def start(installed, package, checkpoint=lambda stage: None):
    descriptor, raw = package
    return Lifecycle(installed, checkpoint=checkpoint).begin(descriptor, confirmed=True, read=lambda _: raw)


def fail_at(stage):
    def checkpoint(value):
        if value == stage:
            raise Crash(value)

    return checkpoint


@pytest.mark.parametrize(
    "stage",
    [
        "prepared",
        "frozen",
        "backup_written",
        "candidate_ready",
        "switching",
        "database_switched",
        "binding_switched",
        "committed",
    ],
)
def test_crash_boundaries_restore_pair_or_finish_commit(installed, package, stage):
    with pytest.raises(Crash):
        start(installed, package, fail_at(stage))
    expect_rollback = stage in {"switching", "database_switched", "binding_switched"}
    if stage == "committed":
        installed.loaded_version = "0.3.6"
    result = Lifecycle(installed).resume()
    if result.get("reenter"):
        installed.loaded_version = "0.3.6"
        result = Lifecycle(installed).resume()
    assert result["result"] == ("rolled_back" if expect_rollback else "upgraded")
    assert result["stage"] == "done" and not installed.path("maintenance.json").exists()
    assert installed.load()["app_version"] == ("0.3.5" if expect_rollback else "0.3.6")


def test_exception_after_durable_commit_never_rolls_back(installed, package):
    def checkpoint(stage):
        if stage == "committed":
            raise OSError("fsync returned after commit")

    with pytest.raises(OSError):
        start(installed, package, checkpoint)
    assert installed.plan()["stage"] == "committed"
    assert installed.load()["app_version"] == "0.3.6"
    with pytest.raises(LurkerError, match="UPGRADE_ALREADY_COMMITTED"):
        Lifecycle(installed).rollback(installed.plan())
    installed.loaded_version = "0.3.6"
    assert Lifecycle(installed).resume()["result"] == "upgraded"


def test_frozen_late_receipt_replayed_on_rollback(installed, watch, clock, package):
    ingest(installed, watch, [publication(watch, 1, clock.now)])
    d = Delivery(installed)
    permit = d.next()
    clock.advance(601)
    with pytest.raises(Crash):
        start(installed, package, fail_at("database_switched"))
    d.report(receipt(permit, clock))
    d.report(receipt(permit, clock))
    assert len(installed.plan()["pending_receipts"]) == 1
    result = Lifecycle(installed).resume()
    assert result["result"] == "rolled_back" and rows(installed)[0]["state"] == "sent"


def test_routine_restore_failure_keeps_business_writes_and_new_pause(installed, watch, package):
    settings = installed.load()
    settings["host"]["routine_id"] = "native-real-fixture"
    from social_lurker.sources import ADAPTER_VERSION

    settings["host"]["evidence_ref"]["sources"]["douyin:normal"] = {
        "adapter_version": ADAPTER_VERSION,
        "level": "recent_pages_verified",
        "evidence": "fixture-pages",
    }
    atomic_json(installed.path("settings.json"), settings)
    result = start(installed, package)
    assert result["reenter"]
    installed.loaded_version = "0.3.6"
    result = Lifecycle(installed).resume()
    assert result["business_writes_open"] and result["routine_restore"]["active"]
    from social_lurker.store import Store

    Store(installed).transition(watch["id"], "paused")
    result = Lifecycle(installed).resume()
    assert not result["routine_restore"]["active"]
    settings = installed.load()
    from social_lurker.operations import routine_plan

    settings["host"]["evidence_ref"]["host"].update(
        routine_binding_hash=routine_plan(installed)["binding_hash"],
        routine_plan_id=installed.plan()["plan_id"],
        routine_active=False,
    )
    atomic_json(installed.path("settings.json"), settings)
    assert Lifecycle(installed).resume()["stage"] == "done"
    assert rows(installed, "watches")[0]["status"] == "paused"


def test_stable_launcher_reenters_target_and_rejects_package_tamper(installed, package):
    result = start(installed, package)
    assert result["reenter"]
    p = subprocess.run(
        [
            sys.executable,
            str(installed.path("run.py")),
            "--instance",
            str(installed.root),
            "--json",
            '{"protocol":1}',
            "maintenance",
            "resume",
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stdout
    assert json.loads(p.stdout)["result"]["stage"] == "done"
    target = installed.path("app/0.3.6/social_lurker/cli.py")
    target.chmod(0o600)
    target.write_text('raise RuntimeError("must never execute")')
    p = subprocess.run(
        [
            sys.executable,
            str(installed.path("run.py")),
            "--instance",
            str(installed.root),
            "--json",
            '{"protocol":1}',
            "status",
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 2 and "ENTRY_STATE_INVALID" in p.stdout and "must never execute" not in p.stderr


def test_package_digest_and_symlink_injection_rejected(installed, package, tmp_path):
    descriptor, raw = package
    bad = {**descriptor, "archive_sha256": "0" * 64}
    with pytest.raises(LurkerError, match="RELEASE_DIGEST_INVALID"):
        prepare(installed, bad, read=lambda _: raw)
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        entry = tarfile.TarInfo("run.py")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "/etc/passwd"
        archive.addfile(entry)
    unsafe = buffer.getvalue()
    bad = {**descriptor, "archive_sha256": digest(unsafe)}
    with pytest.raises(LurkerError, match="RELEASE_PATH_INVALID"):
        prepare(installed, bad, read=lambda _: unsafe)
    assert installed.load()["app_version"] == "0.3.5" and installed.plan() is None


def test_prepared_waits_for_sending_and_normal_pause_remains_available(installed, watch, package, clock):
    from social_lurker.store import Store

    ingest(installed, watch, [publication(watch, 1, clock.now)])
    permit = Delivery(installed).next()
    result = start(installed, package)
    assert result["waiting_for_receipts"]
    Store(installed).transition(watch["id"], "paused")
    Delivery(installed).report(receipt(permit, clock))
    result = Lifecycle(installed).resume()
    assert result["reenter"]
    installed.loaded_version = "0.3.6"
    Lifecycle(installed).resume()
    assert rows(installed)[0]["state"] == "sent" and rows(installed, "watches")[0]["status"] == "paused"


def test_done_archive_interruption_reenters_selected_version(installed, package, monkeypatch):
    import social_lurker.lifecycle as module

    start(installed, package)
    installed.loaded_version = "0.3.6"
    original = module.atomic_json

    def fail_archive(path, value):
        if path.name == "maintenance-result.json":
            raise OSError("crash archiving done")
        return original(path, value)

    monkeypatch.setattr(module, "atomic_json", fail_archive)
    with pytest.raises(OSError):
        Lifecycle(installed).resume()
    assert installed.plan()["stage"] == "done" and installed.plan()["business_writes_open"]
    monkeypatch.setattr(module, "atomic_json", original)
    assert Lifecycle(installed).resume()["stage"] == "done" and installed.plan() is None
