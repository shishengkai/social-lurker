# 盯梢者 · social-lurker

运行在 Grok Bot 电脑上的社交作品全文资料库。首版支持微信视频号与抖音，TikHub 获取、ffmpeg 压缩、fal.ai Whisper 转写、当前 Grok Bot 自身 LLM 校对。

**当前是首版开发实现，尚未通过 Grok Bot 安装、原生校对、通知及强静默验收。** 没有创建 routine、部署、发布正式 Release 或替用户点 Star。

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
uv run python tools/install_dev.py --root /tmp/social-lurker-demo --binding-confirmed
```

命令返回 instance_id 与 launcher。按 [JSON 协议](skills/social-lurker/references/protocol.md) 使用结构化 stdin 调用，先配置当前实例 `.env` 再 doctor。`.env.example` 无真实值。配置不从工作目录或宿主环境推断，不跨 Bot 改写 os.environ。

## Grok Bot

安装入口为 [social-lurker skill](skills/social-lurker/SKILL.md)，验证步骤见 [目标环境验收](skills/social-lurker/references/acceptance.md)。默认 delivery.verified=false，真实消息能力验证后才能正式领取通知。程序提供确定性校对与发送交接，不假设存在未经验证的 Grok Bot HTTP API。

本地布局：

```text
/workspace/social-lurker/
  runtime/launcher.py
  runtime/releases/<version>/{app,skills,vendor,.venv,manifest.json}
  runtime/locks/
  bots/<instance_id>/
    settings.json
    .env
    lurker.sqlite3
    work/<work_id>/<cycle>-<attempt>/
    logs/
    maintenance/
```

源码职责及恢复边界见 [实现说明](docs/implementation.md)，实测结果见 [验证记录](docs/validation.md)。

## 升级与发布

程序只发现固定仓库的最高不可变稳定 Release，解析标签到 commit 并核验 manifest/资产摘要。升级一次授权、逐实例切版本、维护期间保留资料，其他 Bot 绑定不变。未发布正式版本时 upgrade check 会如实报告。

[发布流程](docs/releasing.md)区分源代码 commit 和发布清单 commit，避免清单包含自身 SHA 造成循环。构建器不自动 commit、push、tag 或发布。

第三方解密资源固定到 upstream commit，MIT 许可证与摘要在 [vendor/wechat-decrypt](vendor/wechat-decrypt/)。只携带本地 JS/WASM，不携带 GUI、服务端或历史样本。
