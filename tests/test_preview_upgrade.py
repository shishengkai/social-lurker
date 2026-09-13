import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ingest, publication
from test_delivery import receipt

from social_lurker import __version__
from social_lurker.delivery import Delivery
from social_lurker.errors import LurkerError
from social_lurker.operations import install
from social_lurker.util import atomic_json

ROOT = Path(__file__).resolve().parents[1]
NEXT_VERSION = __version__.rsplit(".", 1)[0] + "." + str(int(__version__.rsplit(".", 1)[1]) + 1)
spec = importlib.util.spec_from_file_location("preview", ROOT / "tools/upgrade_preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def target(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "skills", repo / "skills")
    (repo / "tools").mkdir()
    shutil.copyfile(ROOT / "tools/launcher.py", repo / "tools/launcher.py")
    shutil.copyfile(ROOT / "LICENSE", repo / "LICENSE")
    init = repo / "src/social_lurker/__init__.py"
    init.write_text(init.read_text().replace(__version__, NEXT_VERSION))
    (repo / ".gitignore").write_text("ignored.py\n")
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://github.com/shishengkai/social-lurker.git")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return repo, git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("failure", ["authorization", "commit", "origin", "dirty", "ignored"])
def test_preview_preflight_never_changes_instance(instance, target, failure):
    repo, commit = target
    expected = {
        "authorization": "PREVIEW_AUTHORIZATION_REQUIRED",
        "commit": "PREVIEW_COMMIT_MISMATCH",
        "origin": "PREVIEW_SOURCE_INVALID",
        "dirty": "SOURCE_DIRTY",
        "ignored": "PREVIEW_UNTRACKED_PACKAGE_FILE",
    }[failure]
    if failure == "commit":
        commit = "0" * 40
    elif failure == "origin":
        git(repo, "remote", "set-url", "origin", "https://example.invalid/other.git")
    elif failure == "dirty":
        (repo / "LICENSE").write_text("changed")
    elif failure == "ignored":
        (repo / "src/social_lurker/ignored.py").write_text("raise SystemExit('untracked')")
    before = {p.name: p.read_bytes() for p in instance.root.iterdir() if p.is_file()}
    with pytest.raises(LurkerError, match=expected):
        preview.upgrade(
            repo, instance.root, commit=commit, bot_id="fixture-bot", confirmed=failure != "authorization"
        )
    assert before == {p.name: p.read_bytes() for p in instance.root.iterdir() if p.is_file()}


def call(instance, *command, data=None):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(instance.path("run.py")),
            "--instance",
            str(instance.root),
            *command,
        ],
        input=json.dumps({"protocol": 1, **(data or {})}).encode(),
        capture_output=True,
    )
    value = json.loads(result.stdout)
    assert result.returncode == 0 and value["ok"], value
    return value["result"]


def updates(instance):
    with sqlite3.connect(instance.path("state.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT * FROM updates ORDER BY id").fetchall()


def test_preview_upgrade_preserves_credentials_receipts_and_reenters_installed_coordinator(
    instance, watch, clock, target
):
    install(instance, source=ROOT)
    ingest(instance, watch, [publication(watch, 1, clock.now), publication(watch, 2, clock.now)])
    delivery = Delivery(instance)
    delivery.report(receipt(delivery.next(), clock))
    delivery.report(receipt(delivery.next(), clock, result="unknown"))
    settings = instance.load()
    settings["host"]["evidence_ref"]["host"].update(native_schedule_verified=False, routine_active=False)
    atomic_json(instance.path("settings.json"), settings)
    old_files = {
        str(p): p.read_bytes() for p in instance.path(f"app/{__version__}").rglob("*") if p.is_file()
    }
    old_env, old_updates = instance.path(".env").read_bytes(), updates(instance)
    repo, commit = target
    with pytest.raises(LurkerError, match="INSTANCE_MISMATCH"):
        preview.upgrade(repo, instance.root, commit=commit, bot_id="another-bot", confirmed=True)
    result = preview.upgrade(repo, instance.root, commit=commit, bot_id="fixture-bot", confirmed=True)
    assert result["ok"] and result["delivery_channel"] == "development_preview"
    assert result["target_commit"] == commit and not result["stable_release_published"]
    assert result["result"]["routine_restore"]["active"] is False
    assert instance.load()["app_version"] == NEXT_VERSION
    with pytest.raises(LurkerError, match="MAINTENANCE_ACTIVE"):
        preview.upgrade(repo, instance.root, commit=commit, bot_id="fixture-bot", confirmed=True)
    plan = instance.plan()
    routine = call(instance, "routine", "plan")
    call(
        instance,
        "setup",
        "bind",
        data={
            "evidence": "fixture-paused-query",
            "host": {
                "routine_active": False,
                "routine_binding_hash": routine["binding_hash"],
                "routine_plan_id": plan["plan_id"],
            },
        },
    )
    assert call(instance, "maintenance", "resume")["result"] == "upgraded"
    assert instance.plan() is None
    assert instance.path(".env").read_bytes() == old_env and updates(instance) == old_updates
    assert all(Path(p).read_bytes() == data for p, data in old_files.items())
    assert (instance.root / plan["backup"] / "snapshot.json").is_file()


def test_source_changed_during_build_is_not_installed(instance, target, monkeypatch):
    install(instance, source=ROOT)
    repo, commit = target
    build = preview.build

    def tamper(source, output, **kwargs):
        (source / "src/social_lurker/cli.py").write_text("raise SystemExit('changed during build')")
        return build(source, output, **kwargs)

    monkeypatch.setattr(preview, "build", tamper)
    original = instance.path("settings.json").read_bytes()
    with pytest.raises(LurkerError, match="PREVIEW_COMMIT_MISMATCH"):
        preview.upgrade(repo, instance.root, commit=commit, bot_id="fixture-bot", confirmed=True)
    assert instance.plan() is None and instance.path("settings.json").read_bytes() == original
    assert not instance.path(f"app/{NEXT_VERSION}").exists()
