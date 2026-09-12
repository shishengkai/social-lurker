"""Derive onboarding actions from current state; no fabricated native platform operations."""

import json
import os
from pathlib import Path

from .config import doctor
from .errors import require
from .util import safe_path


def skill_registrations():
    roles = {
        "social-lurker": "盯梢者日常关注、历史收集和完整文案通知；安装配置转 setup，升级转 upgrader",
        "social-lurker-setup": "盯梢者安装、配置、自检和恢复未完成的设置",
        "social-lurker-upgrader": "盯梢者稳定版本检查、升级与恢复",
    }
    return [
        {
            "name": name,
            "description": description,
            "instructions": (
                "先读取当前 Bot 持久指令中的 SOCIAL_LURKER_INSTANCE_ID 和数据根目录。"
                "从该实例 settings.json 获取 runtime_version，读取该版本的 "
                f"skills/{name}/SKILL.md 及其按需引用的说明，再执行任务。"
                "只允许当前 Bot 的独立实例，不能从显示名或其他 Bot 的目录推断。"
                "没有绑定或尚未安装时，从 https://github.com/shishengkai/social-lurker "
                "的 README 进入安装配置 skill；不要把安装命令交给用户搬运。"
                "本共享入口不保存任何 Bot 的 UUID、版本绑定、Key 或业务数据。"
            ),
        }
        for name, description in roles.items()
    ]


def configure_tool_path(root):
    """Also applied in routines: packages installed into the project need no shell activation."""
    record = safe_path(root, "runtime/tools/environment.json")
    if record.exists():
        values = json.loads(record.read_text())
        directories = values["bin_dirs"]
        require(
            isinstance(directories, list)
            and all(
                isinstance(p, str) and Path(p).is_absolute() and os.pathsep not in p for p in directories
            ),
            "CONFIG_INVALID",
            "安装依赖路径无效",
        )
        current = os.environ.get("PATH", "").split(os.pathsep)
        os.environ["PATH"] = os.pathsep.join(dict.fromkeys(directories + current))


def status(instance, settings, store):
    report = doctor(instance, settings)
    report["checks"]["database"] = (
        store.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        and not store.execute("PRAGMA foreign_key_check").fetchall()
    )
    missing_keys = [key for key, value in instance.credentials().items() if not value]
    actions = []
    if missing_keys:
        actions.append(
            {"action": "credentials", "missing_fields": missing_keys, "path": str(instance.path / ".env")}
        )
    missing_tools = [
        key
        for key, value in report["checks"].items()
        if not value and key not in ("credentials", "delivery_verified")
    ]
    if missing_tools:
        actions.append({"action": "environment", "failed_checks": missing_tools})
    if not settings["delivery"]["verified"]:
        actions.append(
            {"action": "native_delivery_test", "instruction": "实际发送与核验消息后才设置 delivery.verified"}
        )
    if not report["routine_interval_synced"]:
        actions.append(
            {
                "action": "native_routine",
                "instruction": "查找本 Bot 已有 routine，创建或更新唯一一项并读回，再同步配置",
            }
        )
    version = settings["runtime_version"]
    package = safe_path(instance.root, "runtime/releases/" + version)
    skill = package / "skills/social-lurker/SKILL.md"
    binding = (
        f"本 Bot 使用盯梢者，SOCIAL_LURKER_INSTANCE_ID={instance.id}。"
        f"数据根目录：{instance.root}。每次根据本实例 settings.json 的 runtime_version "
        "读取对应版本的 skills/social-lurker/SKILL.md，并遵循其中的路由与协议。"
        "修改 Bot 名称保留此实例；复制为新 Bot 必须重新运行 setup 并建立新实例。"
    )
    routine = (
        f"为当前 Bot 执行盯梢者；实例 {instance.id}，数据根 {instance.root}。"
        "读取本实例 settings.json 的 runtime_version，再读取该版本 skills/social-lurker/SKILL.md。"
        "调用 launcher 的 tick，按 skill 完成当前 Bot 原生校对与逐作品全文发送、真实回执。"
        "无更新、无待发消息、无需要处理的问题时不发总结或空轮消息。"
        "后台不检查升级、不邀请 Star、不新增其他 routine。"
        "发送结果不明时保留核对，不能自动补发；不能截断或拆分全文。"
    )
    if settings["platform_bot_id"]:
        routine += f"每次 payload.platform_bot_id 使用 {settings['platform_bot_id']}。"
    return {
        "instance_id": instance.id,
        "runtime_version": version,
        "next_actions": actions,
        "credentials_missing": missing_keys,
        "binding_instructions": binding,
        "skill_registrations": skill_registrations(),
        "active_skill": str(skill),
        "routine": {
            "name": "social-lurker:" + instance.id,
            "interval_minutes": settings["check_interval_minutes"],
            "timezone": settings["timezone"],
            "instructions": routine,
        },
        "checks": report["checks"],
        "native_acceptance": "绑定指令持久保存、真实通知和原生 routine 仍由 setup skill 验证；程序不代替平台回执",
    }
