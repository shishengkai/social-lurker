#!/usr/bin/env python3
"""Python 3.9+ bootstrap. No application dependencies, shell profile edits or credentials."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
MAMBA = {
    "x86_64": ("linux-64", "e7274528ceb9c20d048a428d6c22d7e02e268f8ffb762c4c365422347c8b8ba2"),
    "aarch64": ("linux-aarch64", "02ed4fe982c8ecbf9c5b1759c07ee29063994842b65fdaab439db77b9b00510a"),
}


class InstallError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def require(condition, code, message):
    if not condition:
        raise InstallError(code, message)


def child(args, *, cwd=None, env=None, input=None, timeout=900, error_json=False):
    try:
        result = subprocess.run(
            [str(a) for a in args],
            cwd=cwd,
            env=env,
            input=input,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("INSTALL_STEP_FAILED", "安装步骤未完成，可用同一实例重试") from None
    if result.returncode and error_json:
        # Only our own worker is allowed to return a public structured error.
        try:
            public_error(json.loads(result.stdout))
        except (ValueError, TypeError, KeyError):
            pass
    require(result.returncode == 0, "INSTALL_STEP_FAILED", "安装步骤失败；保留原有实例，请检查依赖和网络")
    return result.stdout


def public_error(response):
    if response.get("error"):
        error = response["error"]
        require(
            isinstance(error.get("code"), str)
            and re.fullmatch(r"[A-Z_]+", error["code"])
            and isinstance(error.get("message"), str),
            "INSTALL_STEP_FAILED",
            "安装步骤未完成",
        )
        raise InstallError(error["code"], error["message"])


def safe(root, relative):
    rel = Path(relative)
    require(not rel.is_absolute() and ".." not in rel.parts, "UNSAFE_PATH", "安装路径越界")
    path = root
    for part in rel.parts:
        path = path / part
        require(not path.is_symlink(), "UNSAFE_PATH", "安装目录不能经过符号链接")
    return path


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".install-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextlib.contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("BUSY", "已有安装或实例任务执行，稍后用相同实例继续") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def probe(python, search_path):
    """Absolute interpreter plus an explicit tool search path; no shell activation required."""
    env = dict(os.environ, PATH=search_path)
    try:
        code = "import json,sys; print(json.dumps({'ready': sys.version_info >= (3,12), 'python': sys._base_executable}))"
        interpreter = json.loads(child([python, "-c", code], env=env, timeout=15))
        if not interpreter["ready"]:
            return None
        binaries = {name: shutil.which(name, path=search_path) for name in ("node", "ffmpeg", "ffprobe")}
        if not all(binaries.values()):
            return None
        node_version = child([binaries["node"], "--version"], env=env, timeout=15).strip()
        if int(node_version.lstrip("v").split(".")[0]) < 22:
            return None
        child([binaries["ffprobe"], "-version"], env=env, timeout=15)
        if "libmp3lame" not in child([binaries["ffmpeg"], "-hide_banner", "-encoders"], env=env, timeout=15):
            return None
        return {
            "python": interpreter["python"],
            "bin_dirs": sorted({str(Path(p).parent) for p in binaries.values()}),
        }
    except (InstallError, ValueError):
        return None


def download_mamba(destination, arch):
    require(
        platform.system() == "Linux" and arch in MAMBA,
        "PLATFORM_UNSUPPORTED",
        "自动补齐依赖支持 Linux x86_64/aarch64；其他开发环境请先准备依赖",
    )
    subdir, expected = MAMBA[arch]
    url = (
        f"https://api.anaconda.org/download/conda-forge/micromamba/2.3.3/{subdir}/micromamba-2.3.3-0.tar.bz2"
    )
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        archive = Path(tmp) / "micromamba.tar.bz2"
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                require(size <= 32 * 1024 * 1024, "DOWNLOAD_TOO_LARGE", "依赖安装器下载超限")
                digest.update(chunk)
                out.write(chunk)
        require(digest.hexdigest() == expected, "DIGEST_MISMATCH", "依赖安装器摘要不匹配")
        with tarfile.open(archive) as tar:
            members = [m for m in tar if m.name == "bin/micromamba"]
            require(len(members) == 1 and members[0].isfile(), "PACKAGE_INVALID", "依赖安装器内容无效")
            with tar.extractfile(members[0]) as data, destination.open("wb") as out:
                shutil.copyfileobj(data, out)
        destination.chmod(0o700)


def environment(root, *, system_only=False):
    tools = safe(root, "runtime/tools")
    tools.mkdir(parents=True, exist_ok=True)
    record = safe(tools, "environment.json")
    if record.exists():
        previous = json.loads(record.read_text())
        path = os.pathsep.join(previous["bin_dirs"] + [os.environ.get("PATH", "")])
        result = probe(previous["python"], path)
        require(
            result is not None,
            "ENVIRONMENT_REPAIR_REQUIRED",
            "已有共享依赖环境不可用；需检查恢复，不自动替换其他 Bot 的依赖",
        )
        return result
    search_path = os.environ.get("PATH", "")
    candidates = [sys.executable] + [
        shutil.which(n) for n in ("python3.12", "python3.13", "python3.14", "python3")
    ]
    for python in dict.fromkeys(p for p in candidates if p):
        result = probe(python, search_path)
        if result:
            save(record, result)
            return result
    require(
        not system_only, "DEPENDENCIES_MISSING", "需要 Python 3.12+、Node 22+、ffmpeg/ffprobe 和 libmp3lame"
    )
    binary = safe(tools, "micromamba")
    # Always verify a fresh installer before executing it; cached binaries are not a trust anchor.
    download_mamba(binary, platform.machine())
    prefix = safe(tools, "environment")
    marker = safe(tools, "environment-pending.json")
    if prefix.exists():
        require(marker.exists(), "ENVIRONMENT_CONFLICT", "已有未知依赖目录，保留原内容")
        require(
            json.loads(marker.read_text()).get("prefix") == str(prefix),
            "ENVIRONMENT_CONFLICT",
            "依赖恢复记录不匹配",
        )
        # Only this bootstrap's unfinished, unpublished prefix can be discarded.
        shutil.rmtree(prefix)
    save(marker, {"prefix": str(prefix), "state": "creating"})
    cache = safe(tools, "package-cache")
    child(
        [
            binary,
            "--no-rc",
            "--no-env",
            "--root-prefix",
            cache,
            "create",
            "--yes",
            "--prefix",
            prefix,
            "--override-channels",
            "--channel",
            "conda-forge",
            "--strict-channel-priority",
            "python=3.12",
            "nodejs=22",
            "ffmpeg=7",
            "pip",
        ],
        timeout=1800,
    )
    result = probe(prefix / "bin/python", str(prefix / "bin") + os.pathsep + search_path)
    require(result is not None, "DEPENDENCIES_MISSING", "依赖安装后自检未通过，可重试恢复")
    # Preserve resolved package provenance. Subsequent runs reuse this environment, never re-solve it.
    packages = []
    for path in sorted((prefix / "conda-meta").glob("*.json")):
        item = json.loads(path.read_text())
        packages.append({k: item.get(k) for k in ("name", "version", "build", "url", "sha256", "md5")})
    save(safe(tools, "dependencies.json"), packages)
    save(record, result)
    marker.unlink(missing_ok=True)
    return result


def source_contents(source, allow_working_tree=False):
    sha = child(["git", "rev-parse", "HEAD"], cwd=source, timeout=15).strip()
    require(re.fullmatch(r"[a-f0-9]{40}", sha), "SOURCE_INVALID", "需使用已固定的源码提交")
    dirty = bool(child(["git", "status", "--porcelain"], cwd=source, timeout=15).strip())
    require(
        not dirty or allow_working_tree,
        "SOURCE_DIRTY",
        "安装用源码存在修改；开发测试需显式使用 --allow-working-tree",
    )
    names = child(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=source, timeout=15
    ).split("\0")
    files = {}
    for name in sorted(set(names)):
        if name.startswith("src/social_lurker/"):
            dest = "app/" + name.removeprefix("src/")
        elif name == "tools/launcher.py":
            dest = "launcher.py"
        elif name.startswith(("skills/", "vendor/")) or name in ("requirements-runtime.txt", "LICENSE"):
            dest = name
        else:
            continue
        path = safe(source, name)
        require(path.is_file(), "SOURCE_INVALID", "源码文件缺失")
        files[dest] = path.read_bytes()
    version_match = re.search(
        r'__version__\s*=\s*["\'](\d+\.\d+\.\d+)["\']', files["app/social_lurker/__init__.py"].decode()
    )
    require(version_match is not None, "SOURCE_INVALID", "源码版本无效")
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    return files, {
        "project": "social-lurker",
        "version": version_match.group(1) + "-dev",
        "channel": "development",
        "source_commit": sha,
        "working_tree": dirty,
        "files": hashes,
        "not_a_stable_release": True,
    }


def make_venv(python, target, requirements):
    child([python, "-m", "venv", target], timeout=120)
    child(
        [
            target / "bin/python",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            requirements,
        ]
    )


def verify_package(folder, manifest):
    require(
        isinstance(manifest.get("files"), dict) and manifest["files"],
        "LEGACY_INSTALL",
        "旧开发安装没有文件清单；请按升级方案迁移，不能覆盖已有版本",
    )
    for name, expected in manifest["files"].items():
        path = safe(folder, name)
        require(
            path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            "PACKAGE_CHANGED",
            "已安装版本校验失败，保留现有数据；不要覆盖共享版本",
        )
    require(
        (folder / ".venv/bin/python").is_file(),
        "INSTALL_INCOMPLETE",
        "已有版本缺少 Python 环境，请检查共享环境恢复",
    )


def preview_package(root, source, python, allow_working_tree=False):
    files, manifest = source_contents(source, allow_working_tree)
    folder = safe(root, "runtime/releases/" + manifest["version"])
    if folder.exists():
        saved = json.loads(safe(folder, "manifest.json").read_text())
        require(
            saved.get("files") == manifest["files"],
            "VERSION_CONTENT_CONFLICT",
            "同名开发版本内容已变化；请使用新版本号，不能覆盖其他 Bot 的代码",
        )
        verify_package(folder, saved)
        return folder
    folder.parent.mkdir(parents=True, exist_ok=True)
    stage = safe(folder.parent, manifest["version"] + ".installing")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    try:
        for name, contents in files.items():
            path = safe(stage, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        make_venv(python, stage / ".venv", stage / "requirements-runtime.txt")
        save(stage / "manifest.json", manifest)
        stage.rename(folder)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return folder


def instance_id(value, platform_bot_id):
    if value:
        try:
            valid = str(uuid.UUID(value)) == value
        except (ValueError, TypeError, AttributeError):
            valid = False
        require(valid, "INSTANCE_BINDING_REQUIRED", "实例 ID 必须是标准 UUID")
        return value
    require(
        platform_bot_id,
        "INSTANCE_BINDING_REQUIRED",
        "请由 setup skill 提供本 Bot 稳定 ID 或先保存新生成的实例 UUID",
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "social-lurker:bot:" + platform_bot_id))


def install(args):
    root = args.root.absolute()
    # Reject symlinks before resolving, including ancestors of the selected root.
    safe(Path(root.anchor), str(root).removeprefix(root.anchor))
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    ident = instance_id(args.instance, args.platform_bot_id)
    data = safe(root, "bots/" + ident)
    with lock(safe(root, "runtime/locks/bootstrap.lock")):
        tools = environment(root, system_only=args.system_only)
        settings_path = safe(data, "settings.json")
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else None
        resumed = settings is not None
        if settings:
            require(
                settings["instance_id"] == ident and settings.get("binding_confirmed"),
                "INSTANCE_BINDING_REQUIRED",
                "已有实例绑定不匹配",
            )
            require(
                settings.get("platform_bot_id") in (None, args.platform_bot_id),
                "INSTANCE_BINDING_REQUIRED",
                "当前 Bot 与已有实例不匹配",
            )
            version = settings["runtime_version"]
            require(re.fullmatch(r"\d+\.\d+\.\d+(?:-dev)?", version), "CONFIG_INVALID", "已有实例版本无效")
            folder = safe(root, "runtime/releases/" + version)
            require(
                folder.is_dir(), "PACKAGE_MISSING", "实例绑定的版本包缺失，需恢复该精确版本，不能改装最新版"
            )
            verify_package(folder, json.loads(safe(folder, "manifest.json").read_text()))
        elif args.channel == "preview":
            folder = preview_package(root, SOURCE, tools["python"], args.allow_working_tree)
        else:
            with tempfile.TemporaryDirectory(dir=safe(root, "runtime/tools"), prefix="stable-loader-") as tmp:
                venv = Path(tmp) / ".venv"
                make_venv(tools["python"], venv, SOURCE / "requirements-runtime.txt")
                command = [
                    venv / "bin/python",
                    SOURCE / "tools/install_stable.py",
                    "--root",
                    root,
                    "--instance",
                    ident,
                    "--binding-confirmed",
                ]
                if args.platform_bot_id:
                    command += ["--platform-bot-id", args.platform_bot_id]
                child(command, error_json=True)
            settings = json.loads(settings_path.read_text())
            folder = safe(root, "runtime/releases/" + settings["runtime_version"])
        launcher = safe(root, "runtime/launcher.py")
        candidate = safe(folder, "launcher.py")
        if launcher.exists():
            require(
                launcher.read_bytes() == candidate.read_bytes(),
                "LAUNCHER_CONFLICT",
                "共享入口不兼容，需先明确其他 Bot 的影响",
            )
        else:
            shutil.copyfile(candidate, launcher)
        # The target package initializes or resumes its own schema/config under its instance lock.
        request = {
            "protocol_version": 1,
            "request_id": "setup:" + ident,
            "payload": {"binding_confirmed": True, "platform_bot_id": args.platform_bot_id},
        }
        output = json.loads(
            child(
                [
                    folder / ".venv/bin/python",
                    "-m",
                    "social_lurker",
                    "--root",
                    root,
                    "--instance",
                    ident,
                    "init",
                    "--request-stdin",
                ],
                cwd=folder / "app",
                input=json.dumps(request),
                timeout=30,
            )
        )
        public_error(output)
        request["payload"] = {"action": "status", "platform_bot_id": args.platform_bot_id}
        output = json.loads(
            child(
                [
                    folder / ".venv/bin/python",
                    "-m",
                    "social_lurker",
                    "--root",
                    root,
                    "--instance",
                    ident,
                    "setup",
                    "--request-stdin",
                ],
                cwd=folder / "app",
                input=json.dumps(request),
                timeout=60,
            )
        )
        public_error(output)
        return {
            "status": "configuration_required" if output["result"]["next_actions"] else "configured",
            "instance_id": ident,
            "runtime_version": json.loads(settings_path.read_text())["runtime_version"],
            "resumed": resumed,
            "launcher": str(launcher),
            "setup_skill": str(folder / "skills/social-lurker-setup/SKILL.md"),
            "result": output["result"],
            "grok_acceptance_pending": True,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="盯梢者安装入口，由 setup skill 编排，无需用户搬运命令")
    parser.add_argument("--root", type=Path, default=Path("/workspace/social-lurker"))
    parser.add_argument("--instance")
    parser.add_argument("--platform-bot-id")
    parser.add_argument("--channel", choices=("stable", "preview"), default="stable")
    parser.add_argument("--system-only", action="store_true")
    parser.add_argument("--allow-working-tree", action="store_true")
    parser.add_argument("--binding-confirmed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        require(
            sys.version_info >= (3, 9),
            "BOOTSTRAP_PYTHON_REQUIRED",
            "安装入口需 Python 3.9+；它会准备运行所需的 3.12+",
        )
        result = install(args)
    except InstallError as error:
        print(
            json.dumps(
                {"status": "blocked", "error": {"code": error.code, "message": error.message}},
                ensure_ascii=False,
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": {
                        "code": "INSTALL_FAILED",
                        "message": "安装未完成，原有资料保留；请检查环境与网络后使用相同实例继续",
                    },
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
