import hashlib
import json
import platform
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .config import doctor, validate
from .errors import LurkerError, require
from .providers.http import client, object_json, request
from .store import ACTIVE_RUNS
from .util import file_lock, now, safe_path, write_json

AUTHORITY = "github.com/shishengkai/social-lurker"
REPO_API = "https://api.github.com/repos/shishengkai/social-lurker"


def semver(value):
    require(
        isinstance(value, str) and re.fullmatch(r"v?\d+\.\d+\.\d+", value),
        "RELEASE_INVALID",
        "稳定版本格式无效",
    )
    return tuple(int(x) for x in value.lstrip("v").split("."))


def is_newer(target, current):
    target_version = semver(target)
    current_version = semver(current.removesuffix("-dev"))
    return target_version > current_version or (
        current.endswith("-dev") and target_version == current_version
    )


def verify_manifest(manifest, version=None):
    require(
        manifest.get("project") == "social-lurker"
        and manifest.get("authority") == AUTHORITY
        and manifest.get("channel") == "stable",
        "RELEASE_INVALID",
        "发布权威或通道无效",
    )
    semver(manifest.get("version"))
    if version:
        require(manifest["version"] == version, "RELEASE_INVALID", "标签与清单版本不一致")
    require(
        isinstance(manifest.get("source_commit"), str)
        and re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"]),
        "RELEASE_INVALID",
        "发布必须固定源码 commit",
    )
    require(
        isinstance(manifest.get("summary"), str) and isinstance(manifest.get("released_at"), str),
        "RELEASE_INVALID",
        "发布说明不完整",
    )
    require(
        manifest.get("launcher_protocol") == {"min": 1, "max": 1},
        "LAUNCHER_UPGRADE_REQUIRED",
        "共享入口协议不兼容，需另行明确跨实例影响",
    )
    require(
        manifest.get("schema_version") == 1 and 1 in manifest.get("migrate_from", []),
        "MIGRATION_UNSUPPORTED",
        "当前程序没有目标数据库迁移路径",
    )
    require(manifest.get("python") == ">=3.12,<4", "RELEASE_INVALID", "Python 兼容声明无效")
    require(
        isinstance(manifest.get("files"), dict) and manifest["files"], "RELEASE_INVALID", "发布文件清单缺失"
    )
    for name, hash_value in manifest["files"].items():
        p = PurePosixPath(name)
        require(
            not p.is_absolute() and ".." not in p.parts and "\\" not in name and name not in ("", "."),
            "RELEASE_INVALID",
            "发布文件路径越界",
        )
        require(
            isinstance(hash_value, str) and re.fullmatch(r"[0-9a-f]{64}", hash_value),
            "RELEASE_INVALID",
            "文件摘要无效",
        )
    required = {
        "app/social_lurker/cli.py",
        "app/social_lurker/schema.sql",
        "requirements-runtime.txt",
        "launcher.py",
        "skills/social-lurker/SKILL.md",
        "vendor/wechat-decrypt/decrypt.cjs",
    }
    if semver(manifest["version"]) >= (0, 2, 0):
        required |= {
            "skills/social-lurker-setup/SKILL.md",
            "skills/social-lurker-upgrader/SKILL.md",
            "skills/social-lurker-upgrader/references/star.md",
            "app/social_lurker/setup.py",
        }
    require(required <= set(manifest["files"]), "RELEASE_INVALID", "发布缺少运行文件")
    return manifest


def discover(http=None):
    http = http or client()
    releases = []
    for page in range(1, 101):
        response = request(http, "GET", REPO_API + "/releases", params={"per_page": 100, "page": page})
        data = response.json()
        require(isinstance(data, list), "RELEASE_INVALID", "Release 列表无效")
        releases.extend(
            r
            for r in data
            if not r.get("draft")
            and not r.get("prerelease")
            and re.fullmatch(r"v\d+\.\d+\.\d+", r.get("tag_name", ""))
        )
        if len(data) < 100:
            break
    else:
        raise LurkerError("RELEASE_INVALID", "无法确认完整 Release 列表")
    require(releases, "RELEASE_UNAVAILABLE", "尚无正式稳定 Release")
    release = max(releases, key=lambda r: semver(r["tag_name"]))
    require(release.get("immutable") is True, "RELEASE_INVALID", "最高正式 Release 尚未锁定为不可变")
    tag = release["tag_name"]
    ref = object_json(request(http, "GET", REPO_API + "/git/ref/tags/" + tag))["object"]
    for _ in range(8):
        if ref["type"] == "commit":
            break
        require(ref["type"] == "tag", "RELEASE_INVALID", "标签未解析到 commit")
        ref = object_json(request(http, "GET", REPO_API + "/git/tags/" + ref["sha"]))["object"]
    require(
        ref["type"] == "commit" and re.fullmatch("[0-9a-f]{40}", ref["sha"]),
        "RELEASE_INVALID",
        "标签解析失败",
    )
    release_commit = ref["sha"]
    # Release metadata is committed AFTER reproducibly building from source_commit.
    # Keeping these two commits explicit avoids a self-referential manifest hash.
    descriptor = object_json(
        request(
            http,
            "GET",
            f"https://raw.githubusercontent.com/shishengkai/social-lurker/{release_commit}/release-manifest.json",
        )
    )
    manifest = verify_manifest(descriptor.get("manifest", {}), tag[1:])
    sha256 = descriptor.get("archive_sha256")
    require(
        isinstance(sha256, str) and re.fullmatch("[0-9a-f]{64}", sha256),
        "RELEASE_INVALID",
        "发布资产整体摘要缺失",
    )
    source_commit = manifest["source_commit"]
    comparison = object_json(request(http, "GET", REPO_API + f"/compare/{source_commit}...{release_commit}"))
    require(
        comparison.get("status") in ("ahead", "identical")
        and comparison.get("merge_base_commit", {}).get("sha") == source_commit,
        "RELEASE_INVALID",
        "源码 commit 不是正式发布 commit 的祖先",
    )
    filename = f"social-lurker-{manifest['version']}.tar.gz"
    assets = [a for a in release.get("assets", []) if a.get("name") == filename]
    require(len(assets) == 1, "RELEASE_INVALID", "发布资产缺失或重复")
    url = assets[0].get("browser_download_url")
    require(
        url == f"https://github.com/shishengkai/social-lurker/releases/download/{tag}/{filename}",
        "RELEASE_INVALID",
        "发布资产地址不匹配",
    )
    return {
        "manifest": manifest,
        "archive_sha256": sha256,
        "asset_url": url,
        "release_commit": release_commit,
    }


def unpack_verified(archive, destination, descriptor):
    with open(archive, "rb") as stream:
        actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    require(actual_hash == descriptor["archive_sha256"], "RELEASE_INVALID", "发布资产摘要不匹配")
    manifest = verify_manifest(descriptor["manifest"])
    expected = set(manifest["files"]) | {"manifest.json"}
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        require(
            len(members) == len(expected) and {m.name for m in members} == expected,
            "RELEASE_INVALID",
            "包文件清单与正式清单不一致",
        )
        require(
            all(m.isfile() and m.size <= 256 * 1024 * 1024 for m in members),
            "RELEASE_INVALID",
            "包含链接、目录或异常大文件",
        )
        for member in members:
            target = safe_path(destination, member.name)
            contents = tar.extractfile(member).read()
            if member.name == "manifest.json":
                require(json.loads(contents) == manifest, "RELEASE_INVALID", "包内清单不匹配")
            else:
                require(
                    hashlib.sha256(contents).hexdigest() == manifest["files"][member.name],
                    "RELEASE_INVALID",
                    "发布文件摘要不匹配",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)


def verify_installed(directory, manifest):
    verify_manifest(manifest)
    for name, expected in manifest["files"].items():
        path = safe_path(directory, name)
        require(
            path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            "RELEASE_INVALID",
            "已安装版本文件校验失败",
        )


def prepare_package(root, descriptor, http=None):
    manifest = verify_manifest(descriptor["manifest"])
    target_os = platform.system().lower()
    require(
        [target_os, platform.machine()] in manifest.get("platforms", []),
        "PLATFORM_UNSUPPORTED",
        "发布产物未支持当前 OS/架构",
    )
    folder = Path(root) / "runtime/releases" / manifest["version"]
    with file_lock(Path(root) / "runtime/locks/install.lock"):
        if folder.exists():
            verify_installed(folder, manifest)
            require(
                (folder / ".venv/bin/python").is_file(), "INSTALL_INCOMPLETE", "现有版本缺少独立 Python 环境"
            )
            return folder
        folder.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".install-", dir=folder.parent) as temporary:
            staging = Path(temporary)
            archive = staging / "download.tar.gz"
            transport = http or client()
            # Asset redirect carries no API credentials; enforce a fixed GitHub asset origin.
            with transport.stream("GET", descriptor["asset_url"], follow_redirects=True) as response:
                from .providers.http import check_response

                check_response(response)
                with archive.open("wb") as stream:
                    total = 0
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        require(total <= 512 * 1024 * 1024, "RELEASE_INVALID", "发布包超过安装上限")
                        stream.write(chunk)
            package = staging / "package"
            unpack_verified(archive, package, descriptor)
            subprocess.run(
                [sys.executable, "-m", "venv", str(package / ".venv")],
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                [
                    str(package / ".venv/bin/python"),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "-r",
                    str(package / "requirements-runtime.txt"),
                ],
                check=True,
                capture_output=True,
                timeout=600,
            )
            # Venv paths move: use Python binary directly, never embedded pip shebang after rename.
            package.rename(folder)
    return folder


def apply_upgrade(instance, store, settings, descriptor):
    manifest = verify_manifest(descriptor["manifest"])
    require(
        is_newer(manifest["version"], settings["runtime_version"]),
        "UPGRADE_NOT_NEWER",
        "目标版本不是更新的稳定版本",
    )
    folder = prepare_package(instance.root, descriptor)
    journal = instance.maintenance / "upgrade.json"
    if not journal.exists():
        write_json(
            journal,
            {
                "stage": "draining",
                "old_version": settings["runtime_version"],
                "new_version": manifest["version"],
                "release_commit": descriptor["release_commit"],
                "created_at": now(),
            },
        )
    record = json.loads(journal.read_text())
    require(record["new_version"] == manifest["version"], "UPGRADE_PENDING", "已有不同目标的升级事务")
    if store.one(f"SELECT id FROM collection_runs WHERE state IN {ACTIVE_RUNS} LIMIT 1"):
        return {
            "pending": True,
            "phase": "draining",
            "next": "tick existing runs; finish proofreading before upgrade apply",
        }
    require(record["stage"] == "draining", "UPGRADE_RECOVERY_REQUIRED", "需先恢复未完成的迁移事务")
    with file_lock(instance.maintenance / "control.lock"):
        backup = instance.maintenance / "upgrade.sqlite3"
        with sqlite3.connect(backup) as target:
            store.conn.backup(target)
        write_json(instance.maintenance / "upgrade-settings.json", settings)
        record["stage"] = "migrating"
        write_json(journal, record)
        try:
            require(
                store.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "DATABASE_INVALID",
                "数据库完整性检查失败",
            )
            require(
                not store.execute("PRAGMA foreign_key_check").fetchall(),
                "DATABASE_INVALID",
                "数据库外键检查失败",
            )
            updated = settings | {"runtime_version": manifest["version"]}
            validate(updated, instance.id)
            verify_installed(folder, manifest)
            require(doctor(instance, updated)["local_ready"], "DOCTOR_FAILED", "新版本本地依赖自检未通过")
            # Run the TARGET program against current data before switching the binding.
            payload = {
                "protocol_version": 1,
                "request_id": "upgrade-doctor",
                "payload": {"platform_bot_id": updated["platform_bot_id"]},
            }
            proc = subprocess.run(
                [
                    str(folder / ".venv/bin/python"),
                    "-m",
                    "social_lurker",
                    "--root",
                    str(instance.root),
                    "--instance",
                    instance.id,
                    "doctor",
                    "--request-stdin",
                ],
                cwd=folder / "app",
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=60,
            )
            require(
                proc.returncode == 0 and json.loads(proc.stdout)["result"]["local_ready"],
                "DOCTOR_FAILED",
                "目标程序自检失败",
            )
            write_json(instance.settings_path, updated)
            record["stage"] = "committed"
            write_json(journal, record)
        except BaseException:
            write_json(instance.settings_path, settings)
            record["stage"] = "draining"
            write_json(journal, record)
            raise
        # No schema migration beyond v1 is registered. Future versions must add transactional migrations.
        backup.unlink(missing_ok=True)
        (instance.maintenance / "upgrade-settings.json").unlink(missing_ok=True)
        journal.unlink()
        return {
            "upgraded": True,
            "version": manifest["version"],
            "release_commit": descriptor["release_commit"],
        }


def recover_upgrade(instance, store):
    path = instance.maintenance / "upgrade.json"
    require(path.exists(), "UPGRADE_NOT_PENDING", "没有待恢复升级")
    record = json.loads(path.read_text())
    if record["stage"] == "migrating":
        backup = instance.maintenance / "upgrade.sqlite3"
        require(backup.exists(), "UPGRADE_RECOVERY_REQUIRED", "迁移备份缺失，禁止开放业务写入")
        with sqlite3.connect(backup) as source:
            source.backup(store.conn)
        write_json(
            instance.settings_path, json.loads((instance.maintenance / "upgrade-settings.json").read_text())
        )
    elif record["stage"] == "committed":
        require(
            store.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
            "DATABASE_INVALID",
            "升级后数据库不完整",
        )
    elif record["stage"] != "draining":
        raise LurkerError("UPGRADE_RECOVERY_REQUIRED", "维护阶段无效")
    for name in ("upgrade.sqlite3", "upgrade-settings.json", "upgrade.json", "upgrade-plan.json"):
        (instance.maintenance / name).unlink(missing_ok=True)
    return {"recovered": True, "database_restored": record["stage"] == "migrating"}
