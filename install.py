#!/usr/bin/env python3
"""Python 3.9+ installer; only Python 3.12 is needed, never shell profile changes."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
MAMBA = {
    "x86_64": ("linux-64", "e7274528ceb9c20d048a428d6c22d7e02e268f8ffb762c4c365422347c8b8ba2"),
    "aarch64": ("linux-aarch64", "02ed4fe982c8ecbf9c5b1759c07ee29063994842b65fdaab439db77b9b00510a"),
}


class InstallError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def require(value, code, message):
    if not value:
        raise InstallError(code, message)


def child(args, **kwargs):
    try:
        r = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=900, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("INSTALL_STEP_FAILED", "安装步骤失败，可在同一位置重试") from None
    require(r.returncode == 0, "INSTALL_STEP_FAILED", "安装步骤未成功；已有数据未被覆盖")
    return r.stdout


def safe(path):
    p = Path(path).absolute()
    require(
        ".." not in p.parts and not any(x.is_symlink() for x in (p, *p.parents)),
        "UNSAFE_PATH",
        "安装目录不能经过符号链接",
    )
    return p


def probe(binary):
    try:
        data = json.loads(
            child(
                [
                    binary,
                    "-I",
                    "-c",
                    "import sys,json;print(json.dumps({'ok':sys.version_info>=(3,12),'path':sys.executable}))",
                ]
            )
        )
        return data["path"] if data["ok"] else None
    except (InstallError, ValueError, KeyError):
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


def interpreter(args):
    candidates = [args.python, sys.executable] + [
        shutil.which(n) for n in ("python3.12", "python3.13", "python3.14", "python3")
    ]
    for binary in dict.fromkeys(p for p in candidates if p):
        result = probe(binary)
        if result:
            return result
    if shutil.which("uv"):
        try:
            result = probe(child(["uv", "python", "find", "3.12"]).strip())
            if result:
                return result
        except InstallError:
            pass
    require(not args.system_only, "PYTHON_REQUIRED", "请准备 Python 3.12+ 或允许安装器准备 Python")
    require(
        args.python_root is not None,
        "PYTHON_DIRECTORY_REQUIRED",
        "缺少 Python 3.12；请由 Bot 指定持久的 --python-root 目录",
    )
    root = safe(args.python_root)
    if (root / "bin/python").exists():
        result = probe(root / "bin/python")
        require(result is not None, "PYTHON_REPAIR_REQUIRED", "已有 Python 环境待修复")
        return result
    require(not root.exists(), "PYTHON_DIRECTORY_CONFLICT", "不会覆盖未知 Python 目录")
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".lurker-bootstrap-", dir=root.parent) as tmp:
        binary = Path(tmp) / "micromamba"
        download_mamba(binary, platform.machine())
        child(
            [
                binary,
                "--no-rc",
                "--no-env",
                "--root-prefix",
                Path(tmp) / "cache",
                "create",
                "--yes",
                "--prefix",
                root,
                "--override-channels",
                "--channel",
                "conda-forge",
                "--strict-channel-priority",
                "python=3.12",
            ]
        )
    result = probe(root / "bin/python")
    require(result is not None, "PYTHON_REPAIR_REQUIRED", "Python 自检失败")
    return result


def worker(args):
    sys.path.insert(0, str(SOURCE / "src"))
    from social_lurker.config import Instance
    from social_lurker.operations import install
    from social_lurker.releases import discover, prepare

    if args.allow_working_tree:
        return install(Instance(args.instance), source=SOURCE, bot_id=args.bot_id)
    descriptor = discover()
    # Verify the official target package before executing its own installer implementation.
    with tempfile.TemporaryDirectory(prefix="lurker-release-") as tmp:
        target = prepare(Instance(str(Path(tmp).resolve())), descriptor)
        code = "import sys,json;sys.path.insert(0,sys.argv[1]);from social_lurker.config import Instance;from social_lurker.operations import install;print(json.dumps(install(Instance(sys.argv[2]),descriptor=json.loads(sys.stdin.read()),bot_id=sys.argv[3] or None),ensure_ascii=False))"
        return json.loads(
            child(
                [sys.executable, "-I", "-B", "-c", code, target, args.instance, args.bot_id or ""],
                input=json.dumps(descriptor),
            )
        )


def main():
    parser = argparse.ArgumentParser(description="盯梢者轻量实例安装；API key 安装后在本实例 .env 填写")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--bot-id")
    parser.add_argument("--python")
    parser.add_argument("--python-root")
    parser.add_argument("--system-only", action="store_true")
    parser.add_argument(
        "--allow-working-tree", action="store_true", help="明确安装当前开发预览；不是正式稳定发布"
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        require(Path(args.instance).is_absolute(), "INSTANCE_REQUIRED", "必须由 Bot 指定独立持久绝对目录")
        safe(args.instance)
        if args.worker:
            require(sys.version_info >= (3, 12), "PYTHON_REQUIRED", "需要 Python 3.12+")
            result = worker(args)
        else:
            python = interpreter(args)
            output = child([python, "-I", "-B", SOURCE / "install.py", *sys.argv[1:], "--worker"])
            result = json.loads(output)
            require(result.get("ok") is True, "INSTALL_FAILED", "安装未完成，请核对实例状态")
            result = result["result"]
            result["python"] = python
        print(json.dumps({"protocol": 1, "ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as error:
        # Provider bodies, secrets and raw subprocess output never enter this error envelope.
        code = getattr(error, "code", "INSTALL_FAILED")
        message = getattr(error, "message", "安装未完成；请核对实例状态后重试")
        print(
            json.dumps(
                {
                    "protocol": 1,
                    "ok": False,
                    "error": {"code": code, "message": message, "retryable": False, "next_action": None},
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
