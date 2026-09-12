import copy
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

from . import __version__
from .errors import LurkerError, require
from .util import atomic_write, safe_path, write_json

DEFAULTS = {
    "schema_version": 1,
    "instance_id": "",
    "runtime_version": __version__,
    "platform_bot_id": None,
    "binding_confirmed": False,
    "timezone": "Asia/Shanghai",
    "check_interval_minutes": 30,
    "processing": {"max_parallel_works": 1, "max_attempts": 3, "retry_delays_seconds": [60, 300]},
    "retention": {"failed_work_days": 3},
    "logging": {"max_file_mb": 10, "backup_count": 5},
    "services": {
        "wechat_channels": {"provider": "tikhub", "api_family": "wechat_channels_v2"},
        "douyin": {"provider": "tikhub", "api_family": "douyin_app_v3"},
        "asr": {
            "provider": "fal",
            "model": "fal-ai/whisper",
            "transport": "http_queue",
            "upload": "fal_cdn",
            "language": None,
            "task": "transcribe",
            "diarize": False,
            "chunk_level": "segment",
        },
        "proofreading": {
            "provider": "grok_bot",
            "enabled": True,
            "segment_max_chars": 6000,
            "context_chars": 300,
        },
    },
    "media": {
        "format": "mp3",
        "container": "mp3",
        "sample_rate_hz": 16000,
        "channels": 1,
        "bitrate_kbps": 24,
        "max_source_bytes": 1073741824,
        "max_audio_bytes": 134217728,
        "max_duration_seconds": 43200,
    },
    "execution": {
        "tick_soft_seconds": 240,
        "max_new_works_per_tick": 10,
        "max_pages_per_tick": 100,
        "lease_seconds": 600,
        "lease_renew_seconds": 30,
        "min_free_bytes": 2147483648,
        "lookback_hours": 48,
    },
    "delivery": {"verified": False, "max_chars": None},
    "routine": {"verified_interval_minutes": None},
}


def validate(settings, instance_id):
    def shape(actual, template, path=""):
        require(isinstance(actual, dict), "CONFIG_INVALID", f"配置对象无效：{path}")
        require(set(template) <= set(actual), "CONFIG_INVALID", f"配置缺少字段：{path}")
        require(set(actual) <= set(template), "CONFIG_INVALID", f"配置含未知字段：{path}")
        for key, value in template.items():
            if isinstance(value, dict):
                shape(actual[key], value, path + key + ".")

    shape(settings, DEFAULTS)
    require(settings["instance_id"] == instance_id, "INSTANCE_BINDING_REQUIRED", "实例绑定不匹配")
    require(settings["schema_version"] == 1, "CONFIG_INVALID", "配置版本不支持")
    require(
        bool(re.fullmatch(r"\d+\.\d+\.\d+(?:-dev)?", settings["runtime_version"])),
        "CONFIG_INVALID",
        "程序版本格式无效",
    )
    try:
        ZoneInfo(settings["timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise LurkerError("CONFIG_INVALID", "时区无效") from None
    require(settings["services"] == DEFAULTS["services"], "CONFIG_INVALID", "首版服务及校对参数固定")
    for key in ("format", "container", "sample_rate_hz", "channels", "bitrate_kbps"):
        require(settings["media"][key] == DEFAULTS["media"][key], "CONFIG_INVALID", "首版音频格式固定")
    for group in ("processing", "retention", "logging", "media", "execution"):
        for key, value in settings[group].items():
            if type(DEFAULTS[group][key]) is int:
                require(type(value) is int and value > 0, "CONFIG_INVALID", f"{group}.{key} 必须为正整数")
    require(
        settings["processing"]["max_parallel_works"] == 1 and settings["processing"]["max_attempts"] == 3,
        "CONFIG_INVALID",
        "首版每 Bot 并发为 1，每阶段最多 3 次尝试",
    )
    delays = settings["processing"]["retry_delays_seconds"]
    require(
        isinstance(delays, list) and len(delays) == 2 and all(type(x) is int and x >= 60 for x in delays),
        "CONFIG_INVALID",
        "重试延迟无效",
    )
    require(
        type(settings["check_interval_minutes"]) is int and settings["check_interval_minutes"] > 0,
        "CONFIG_INVALID",
        "检查间隔无效",
    )
    cap = settings["delivery"]["max_chars"]
    require(cap is None or (type(cap) is int and cap > 0), "CONFIG_INVALID", "消息上限无效")
    require(
        type(settings["delivery"]["verified"]) is bool and type(settings["binding_confirmed"]) is bool,
        "CONFIG_INVALID",
        "绑定或交付验证状态无效",
    )
    require(
        settings["platform_bot_id"] is None or isinstance(settings["platform_bot_id"], str),
        "CONFIG_INVALID",
        "Bot 标识无效",
    )
    return settings


class Instance:
    def __init__(self, root, instance_id):
        try:
            require(
                str(uuid.UUID(instance_id)) == instance_id,
                "INSTANCE_BINDING_REQUIRED",
                "需显式 UUID 实例标识",
            )
        except (ValueError, TypeError, AttributeError):
            raise LurkerError("INSTANCE_BINDING_REQUIRED", "需显式 UUID 实例标识") from None
        self.root = Path(root).resolve()
        self.id = instance_id
        self.path = safe_path(self.root, f"bots/{instance_id}")
        self.settings_path = safe_path(self.path, "settings.json")
        self.db_path = safe_path(self.path, "lurker.sqlite3")
        self.maintenance = safe_path(self.path, "maintenance")

    def initialize(self, *, binding_confirmed=False, platform_bot_id=None, runtime_version=None):
        require(
            binding_confirmed is True, "INSTANCE_BINDING_REQUIRED", "先明确当前 Bot 绑定；复制 Bot 须新实例"
        )
        if self.settings_path.exists():
            return self.load(platform_bot_id=platform_bot_id)
        if runtime_version is None:
            package_manifest = Path(__file__).resolve().parents[2] / "manifest.json"
            runtime_version = __version__
            if package_manifest.is_file():
                installed = json.loads(package_manifest.read_text())
                require(installed.get("project") == "social-lurker", "CONFIG_INVALID", "安装清单项目不匹配")
                runtime_version = installed["version"]
        for folder in ("work", "logs", "maintenance"):
            safe_path(self.path, folder).mkdir(parents=True, exist_ok=True, mode=0o700)
        settings = copy.deepcopy(DEFAULTS)
        settings.update(
            instance_id=self.id,
            runtime_version=runtime_version,
            binding_confirmed=True,
            platform_bot_id=platform_bot_id,
        )
        validate(settings, self.id)
        if not (self.path / ".env").exists():
            atomic_write(self.path / ".env", "TIKHUB_API_KEY=\nFAL_KEY=\n")
        write_json(self.settings_path, settings)
        return settings

    def load(self, *, platform_bot_id=None):
        try:
            settings = validate(json.loads(self.settings_path.read_text()), self.id)
        except (OSError, ValueError, TypeError, KeyError):
            raise LurkerError("CONFIG_INVALID", "settings.json 不可读取或格式无效") from None
        require(settings["binding_confirmed"], "INSTANCE_BINDING_REQUIRED", "实例尚未绑定")
        if settings["platform_bot_id"] is not None:
            require(
                platform_bot_id == settings["platform_bot_id"],
                "INSTANCE_BINDING_REQUIRED",
                "Bot 稳定标识不匹配",
            )
        return settings

    def credentials(self):
        # Never interpolate the host environment or call load_dotenv/os.environ.update.
        try:
            values = dotenv_values(safe_path(self.path, ".env"), interpolate=False)
        except (OSError, UnicodeError):
            raise LurkerError("CREDENTIALS_INVALID", ".env 不可读取") from None
        return {k: values.get(k) for k in ("TIKHUB_API_KEY", "FAL_KEY")}

    def set_credentials(self, values):
        require(
            isinstance(values, dict) and set(values) <= {"TIKHUB_API_KEY", "FAL_KEY"},
            "INVALID_REQUEST",
            "不支持的凭据字段",
        )
        merged = self.credentials() | values
        for value in merged.values():
            require(
                value is None or (isinstance(value, str) and not any(c in value for c in "\n\r\x00")),
                "INVALID_REQUEST",
                "凭据格式无效",
            )
        lines = []
        for key, value in merged.items():
            escaped = (value or "").replace("\\", "\\\\").replace("'", "\\'")
            lines.append(f"{key}='{escaped}'\n")
        atomic_write(self.path / ".env", "".join(lines))


def doctor(instance, settings):
    checks = {"python": sys.version_info >= (3, 12), "ffmpeg": False, "ffprobe": False, "node": False}
    for name in ("ffmpeg", "ffprobe", "node"):
        binary = shutil.which(name)
        if binary:
            try:
                p = subprocess.run(
                    [binary, "-version" if name != "node" else "--version"], capture_output=True, timeout=15
                )
                checks[name] = p.returncode == 0
                if name == "node":
                    checks[name] = (
                        checks[name] and int(p.stdout.decode().strip().lstrip("v").split(".")[0]) >= 22
                    )
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
    if checks["ffmpeg"]:
        p = subprocess.run(
            [shutil.which("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True, timeout=15
        )
        checks["mp3_encoder"] = b"libmp3lame" in p.stdout
    vendor = Path(__file__).resolve().parents[2] / "vendor/wechat-decrypt"
    try:
        provenance = json.loads((vendor / "provenance.json").read_text())
        checks["decrypt_assets"] = (
            all(
                hashlib.sha256((vendor / name).read_bytes()).hexdigest() == expected
                for name, expected in provenance["files"].items()
            )
            and (vendor / "decrypt.cjs").is_file()
        )
    except (OSError, ValueError, KeyError, TypeError):
        checks["decrypt_assets"] = False
    checks["credentials"] = all(instance.credentials().values())
    checks["database"] = instance.db_path.exists()
    checks["delivery_verified"] = settings["delivery"]["verified"]
    return {
        "checks": checks,
        "os": platform.system(),
        "arch": platform.machine(),
        "local_ready": all(v for k, v in checks.items() if k != "delivery_verified"),
        "grok_acceptance_pending": True,
        "upload_retention": {"requested_seconds": 172800, "remote_expiry_verified": False},
        "routine_interval_synced": settings["routine"]["verified_interval_minutes"]
        == settings["check_interval_minutes"],
    }
