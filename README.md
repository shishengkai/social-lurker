# 盯梢者 · social-lurker

运行在 Grok Bot 电脑上的社交作品全文资料库。首版支持微信视频号与抖音，TikHub 获取、ffmpeg 压缩、fal.ai Whisper 转写、当前 Grok Bot 自身 LLM 校对。

**当前是首版开发实现，尚未通过 Grok Bot 安装、原生校对、通知及强静默验收。** 没有创建 routine、部署、发布正式 Release 或替用户点 Star。

## 安装到 Grok Bot

在你希望使用的 Bot 中发送一句话：

> 请安装并配置盯梢者：https://github.com/shishengkai/social-lurker

Bot 会读取 [安装配置 skill](skills/social-lurker-setup/SKILL.md)，准备环境、绑定当前 Bot、引导填写 TikHub 和 fal.ai Key，并验证通知和定时任务。用户只需提供实际缺失的信息；默认每 30 分钟检查，由当前 Bot 自己的 LLM 校对，不需要额外 LLM Key。

**给执行安装的 Bot：当前安装通道为 `preview`，版本为 `0.2.0-dev`。** 使用固定到精确 commit 的干净源码快照，按 setup skill 调用根目录 `install.py --channel preview`。当前没有正式 Release；这是明确的试用通道，不能称 stable。正式发布后入口改用 stable。完整命令和验证规则由 skill 负责，不要求用户复制工程说明。

同一 Bot 再次安装会继续原配置，新增 Bot 复用程序并建立独立数据库和 .env。重复安装不自动升级已有实例。原生安装、通知和静默能力仍须在 Grok Bot 中取得真实证据。

- 每个 Bot 独立 SQLite、settings.json 与 .env；跨 Bot 不合并资料或去重。
- 原稿和完整校对稿长期存 SQLite，每条新作品一条完整通知；历史批次只有结束汇总。
- 不增加 Web server、阅读文件或额外 LLM API。超长单条消息阻塞并保留全文，不自动摘要/截断/拆分。
- 停止后在途工作完成保存，待发通知取消；恢复从现在开始。
- 已知 ASR 任务继续查询，提交/发送结果不明时保留核对状态，不盲目重复。

## 本地开发

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/)、ffmpeg/ffprobe；视频号解密另需 Node 22+。系统依赖不由测试自动安装。

```sh
uv sync --python 3.12
uv run pytest -q
uv run ruff check src tests tools
uv run python tools/dev.py --help
```

使用 `tools/dev.py` 从源码启动，避免 macOS iCloud 自动设置隐藏属性使 editable `.pth` 被 Python 忽略。部署包采用独立 app 目录，不依赖 editable 安装。依赖版本与摘要固定在 uv.lock 和 requirements-runtime.txt。

开发安装（只用于验证，根目录可自行选择；不会自动绑定真实 Bot 或创建 routine）：

```sh
uv run python tools/install_dev.py --root /tmp/social-lurker-demo --instance <固定的实例UUID>
```

命令返回 instance_id 与 launcher。按 [JSON 协议](skills/social-lurker/references/protocol.md) 使用结构化 stdin 调用，先配置当前实例 `.env` 再 doctor。`.env.example` 无真实值。配置不从工作目录或宿主环境推断，不跨 Bot 改写 os.environ。

## Grok Bot

用户入口由三个 skill 配合完成：

| Skill | 职责 |
| --- | --- |
| [social-lurker](skills/social-lurker/SKILL.md) | 日常盯梢、全文处理与通知，以及请求路由 |
| [social-lurker-setup](skills/social-lurker-setup/SKILL.md) | 安装、绑定、配置和恢复未完成的设置 |
| [social-lurker-upgrader](skills/social-lurker-upgrader/SKILL.md) | 稳定版本检查、升级与恢复 |

Star 为安装/升级成功后的共用可选步骤，用户独立授权；不增加单独 skill。验证步骤见 [目标环境验收](skills/social-lurker/references/acceptance.md)。默认 delivery.verified=false，真实消息能力验证后才能正式领取通知。

本地布局：

```text
/workspace/social-lurker/
  runtime/launcher.py
  runtime/releases/<version>/{app,skills,vendor,.venv,manifest.json}
  runtime/locks/
  runtime/tools/                 # 自动准备或记录的依赖环境
  bots/<instance_id>/
    settings.json
    .env
    lurker.sqlite3
    work/<work_id>/<cycle>-<attempt>/
    logs/
    maintenance/
```

源码职责及恢复边界见 [实现说明](docs/implementation.md)，实测结果见 [验证记录](docs/validation.md)。

安装器的前提、恢复边界及依赖来源见 [安装实现](docs/installation.md)。

## 升级与发布

程序只发现固定仓库的最高不可变稳定 Release，解析标签到 commit 并核验 manifest/资产摘要。升级一次授权、逐实例切版本、维护期间保留资料，其他 Bot 绑定不变。未发布正式版本时 upgrade check 会如实报告。

[发布流程](docs/releasing.md)区分源代码 commit 和发布清单 commit，避免清单包含自身 SHA 造成循环。构建器不自动 commit、push、tag 或发布。

第三方解密资源固定到 upstream commit，MIT 许可证与摘要在 [vendor/wechat-decrypt](vendor/wechat-decrypt/)。只携带本地 JS/WASM，不携带 GUI、服务端或历史样本。
