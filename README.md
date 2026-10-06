# 盯梢者（social-lurker）

给 Bot 或 AI agent 的作品动态查询 CLI。支持抖音作者主页/作品链接、可识别的抖音短链，以及微信视频号 `sph` 分享链接；输出作者与作品元信息。**安装和配置从下面开始，无需先克隆仓库、构建源码、安装 skill 或准备开发环境。**

当前正式版本：[v0.5.1](https://github.com/shishengkai/social-lurker/releases/tag/v0.5.1)。运行环境：**macOS 或 Linux，Python 3.12+**；运行依赖只有 Python 标准库，Windows 暂不支持。

## 给 Bot / agent 的执行说明

用户把这个仓库网址交给你安装时，按以下顺序执行：

1. 检查系统、Python 和是否已有纯 CLI 安装。首次安装用正式 Release 包；已有纯 CLI 用“升级”，不重装。
2. 完成下载、SHA256 校验、安装与免费验收。不要把开发构建当成正式安装。
3. 创建或选择该 Bot 使用的 profile，记住返回的真实 ID；所有业务命令必须显式选择它。
4. 用户提供 TikHub API key 后，通过隐藏输入或安全的 stdin 配置；不把 key 写进聊天、命令参数或日志。
5. 报告版本、入口、profile ID 和免费验收结果。尚未收到作者链接和检查频率时，在此结束；安装本身不需要付费 API。

遇到失败时停止并报告错误码及失败步骤。不要自行写包装器、改安装文件、手工写库或切换供应商路径绕过错误。已有目录、旧实例、凭据和数据不删除、不自动导入。

## 首次安装

以下命令用于 **POSIX shell（sh/bash/zsh）**。需要 `curl`、`tar` 和可持续使用的 Python 3.12+。如果 `python3` 太旧，把 `PYTHON=python3` 改为机器上已有的 `python3.12` 或合适的绝对路径；缺少运行环境时先报告，不继续安装。

默认目录：

| 用途 | 位置 |
| --- | --- |
| 程序 | `~/.local/opt/social-lurker-cli` |
| 数据与 profile | `~/.local/share/social-lurker-cli` |
| 共享凭据 | `~/.config/social-lurker-cli/credentials.json` |
| 命令入口 | `~/.local/opt/social-lurker-cli/bin/social-lurker` |

安装器只接受空白安装/数据目录。已有这些目录时先查明来源；已有纯 CLI 请使用升级步骤，旧版本实例不要原地覆盖。

下面下载固定的不可变 v0.5.1 资产，**先校验，再解包和执行安装器**。无需 Git、GitHub CLI、uv、pip 或 sudo。

```sh
set -eu
PYTHON=python3
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "需要 Python 3.12+"; import fcntl'
command -v curl >/dev/null
command -v tar >/dev/null

SL_PACKAGE_DIR="$(mktemp -d)"
SL_RELEASE_URL='https://github.com/shishengkai/social-lurker/releases/download/v0.5.1'
curl --fail --location --retry 3 "$SL_RELEASE_URL/social-lurker-cli-0.5.1.tar.gz" \
  --output "$SL_PACKAGE_DIR/social-lurker-cli-0.5.1.tar.gz"
curl --fail --location --retry 3 "$SL_RELEASE_URL/social-lurker-cli-0.5.1.manifest.json" \
  --output "$SL_PACKAGE_DIR/social-lurker-cli-0.5.1.manifest.json"

"$PYTHON" - "$SL_PACKAGE_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
expected = {
    "social-lurker-cli-0.5.1.tar.gz": "d33d58bb0f38327da6566a9821b369c3fe2c381dec1b48b616390a7576df9378",
    "social-lurker-cli-0.5.1.manifest.json": "357ca553ff25300d2f6465e8fbfd53750474c263c74a8691d41000afcef5f1cc",
}
for name, digest in expected.items():
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if actual != digest:
        raise SystemExit("SHA256 校验失败：" + name)
m = json.loads((root / "social-lurker-cli-0.5.1.manifest.json").read_text())
if (m["version"], m["source_state"], m["git_sha"]) != (
    "0.5.1", "clean", "8e83edb7eb64f5035cb4920319c87d8bfe04c039"
):
    raise SystemExit("发行清单不匹配")
print("正式包与清单校验通过")
PY

mkdir "$SL_PACKAGE_DIR/unpacked"
tar -xzf "$SL_PACKAGE_DIR/social-lurker-cli-0.5.1.tar.gz" -C "$SL_PACKAGE_DIR/unpacked"
"$PYTHON" "$SL_PACKAGE_DIR/unpacked/install.py" \
  --package "$SL_PACKAGE_DIR/social-lurker-cli-0.5.1.tar.gz"

SL_CLI="$HOME/.local/opt/social-lurker-cli/bin/social-lurker"
"$SL_CLI" --version
"$SL_CLI" --help
```

成功时安装器返回 `installed: true`，版本为 `0.5.1`。安装完成后始终使用上述 `SL_CLI` 入口，它绑定安装时选定的 Python 和数据根；不要删除该 Python 环境。临时下载目录不参与后续运行。

要使用其他目录，在安装命令末尾加 `--install-root /绝对路径/程序 --data-root /绝对路径/数据`，随后把 `SL_CLI` 指向所选程序目录下的 `bin/social-lurker`。凭据路径可通过命令前的全局参数 `--credentials-file /绝对路径/credentials.json` 指定。

## 创建 profile 与免费验收

profile 隔离关注列表和数据库。编号自动分配为 p0001、p0002…，显示标签不是 ID；没有默认空间或全局“当前空间”。新 Bot 通常创建自己的 profile，不复用其他 Bot 的空间。

```sh
SL_CLI="$HOME/.local/opt/social-lurker-cli/bin/social-lurker"
"$SL_CLI" profile list
"$SL_CLI" profile create --label '自媒体'
"$SL_CLI" profile list
```

从输出中读取 `profile_id`，保存为这个 Bot 的固定配置。下面的 `p0001` **必须替换为实际返回的 ID**，不能假定每台机器都相同。

```sh
SL_PROFILE_ID=p0001
"$SL_CLI" --profile "$SL_PROFILE_ID" list
"$SL_CLI" --profile "$SL_PROFILE_ID" check
"$SL_CLI" config status
```

新建且无作者的 profile 执行 `check` 不访问 TikHub，无需密钥。验收应看到版本 0.5.1、正确 profile、空关注列表、成功的 `complete` 和 `summary.requests=0`。`config status` 只显示是否配置及来源，不输出 key。

## 配置 TikHub 凭据

用户在终端输入时：

```sh
"$SL_CLI" config set-key
"$SL_CLI" config status
```

`set-key` 隐藏输入，原子保存凭据文件，权限为 `0600`。没有交互终端的 agent 使用 `config set-key --stdin`，由宿主安全凭据通道向 stdin 提供一行 key；**不要把真实 key 写在命令文本、脚本、聊天或日志里**。没有安全输入通道时，等待用户配置。

凭据文件优先于 `TIKHUB_API_KEY` 环境变量；文件中配置项为空或无效时报错，不静默切换到环境变量。共享 key 跨 profile 使用时，外围安排错峰。

## 添加作者与查询（会调用 TikHub）

收到用户的真实作者/作品链接，且获得业务调用授权后再执行。下面是命令形式，`LINK` 与 `AUTHOR_ID` 都是占位值，不直接运行。

```sh
"$SL_CLI" --profile "$SL_PROFILE_ID" add 'LINK'
"$SL_CLI" --profile "$SL_PROFILE_ID" list
"$SL_CLI" --profile "$SL_PROFILE_ID" check
"$SL_CLI" --profile "$SL_PROFILE_ID" --format json check
"$SL_CLI" --profile "$SL_PROFILE_ID" unfollow --platform douyin --author-id AUTHOR_ID
```

- 抖音支持 `/user/<作者ID>`、`/video/<作品ID>`、`/note/<作品ID>` 及可识别短链；作品链接用于确定所属作者。视频号支持 `https://weixin.qq.com/sph/...`，未知形式报错。
- `add` 只登记作者首页作为基线，不输出新增、不导入全部历史。重复 active add 幂等；重新关注仍登记当下首页。
- `check` 从首页开始。未入库的稳定作品 ID 都算新增，包括老发布时间的作品；非空页全新增且有下一页才续页，混合页完整处理后停止。
- 默认 JSONL，逐条刷新；最后有 `complete`。完整 JSON 使用 `--format json`。没有完成记录不能视为整轮成功；失败、部分失败和正常无新增要区分。
- 无 ack、队列、重放或跨运行续页恢复；允许错过。`unfollow` 保留历史。

全局参数必须放在子命令前。调度、展示、通知由 Bot/外围负责，程序不创建定时任务、不发送消息、不提供 skill 或 daemon。不在未获用户授权时自动添加作者、调用付费接口或启用调度。

## 已有纯 CLI：显式升级

如果命令入口已经存在，先确认版本和 profile，不运行首次安装器：

```sh
SL_CLI="$HOME/.local/opt/social-lurker-cli/bin/social-lurker"
"$SL_CLI" --version
"$SL_CLI" profile list
"$SL_CLI" upgrade check
"$SL_CLI" upgrade apply --version 0.5.1
"$SL_CLI" --version
"$SL_CLI" profile list
```

升级会验证正式不可变 Release、tag、manifest 与包摘要；保留 profile、数据、配置、旧程序版本和备份。0.5.0→0.5.1 不需要 schema 迁移。只有显式 upgrade 查询/应用软件版本，日常业务不自动更新；不保留 Star。旧 0.3.6 实例不是这个升级流程的输入，不迁移或清理它。

## 常见失败

| 现象/错误码 | 下一步 |
| --- | --- |
| Python 版本不满足、Windows、缺少 curl/tar | 报告环境缺项，先准备支持的环境 |
| 安装目录非空 | 查明是否已有纯 CLI；已有则升级，不删除目录 |
| `PROFILE_NOT_FOUND` / 缺少 `--profile` | 用 profile list 核对真实 ID，显式传入 |
| `CREDENTIAL_MISSING` / `CONFIG_INVALID` | 用 config status 和安全输入核对配置，不输出凭据 |
| `HTTP_BLOCKED` | 上游网关拦截，不能据此认定 key 无效；停止并报告 |
| `AUTH_FAILED` / `QUOTA_UNAVAILABLE` | 报告认证/权限或额度问题，不自动重复调用 |
| `SOURCE_LINK_INVALID` | 报告不支持的链接形式，等待有效链接 |
| `PROFILE_BUSY` / `MAINTENANCE_BUSY` | 让外围错峰，避免并发业务与升级 |

## 开发与详细协议

以下仅供修改源码的开发者，安装使用者不需要执行：

```sh
uv sync --frozen --group dev
PYTHONPATH=src uv run python -m social_lurker --help
uv run pytest -q
uv run ruff check install.py src tests tools
uv run ruff format --check install.py src tests tools
uv run python tools/smoke_install.py
```

开发时显式指定独立安装/数据根。详见 [实现协议](docs/implementation.md)、[安装升级机制](docs/installation.md) 和 [验收记录](docs/validation.md)。规范来自关联 social-lurker-brain 的 0.5.0 开发文档；0.5.1 是其兼容补丁。

v0.5.1 已通过 132 项测试、Linux/macOS × Python 3.12/3.13 CI、正式包安装与 0.5.0→0.5.1 升级验证。公开抖音作者短链解析已实测；付费平台接口完整覆盖、Grok 宿主网关恢复与实际增量仍待使用验证。
