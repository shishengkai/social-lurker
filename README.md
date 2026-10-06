# 盯梢者（social-lurker）

给 Bot 或 AI agent 的作品动态查询 CLI。支持抖音作者主页/作品链接、可识别的抖音短链，以及微信视频号 `sph` 分享链接；输出作者与作品元信息。**安装和配置从下面开始，无需先克隆仓库、构建源码、安装 skill 或准备开发环境。**

当前正式版本：[v0.5.2](https://github.com/shishengkai/social-lurker/releases/tag/v0.5.2)。运行环境：**macOS 或 Linux，Python 3.12+**；运行依赖只有 Python 标准库，Windows 暂不支持。

## 给 Bot / agent 的执行说明

用户把这个仓库网址交给你安装时，按以下顺序执行：

1. 检查系统、Python 和是否已有纯 CLI 安装。首次安装用正式 Release 包；已有纯 CLI 用“升级”，不重装。
2. 完成下载、SHA256 校验、安装自检。不要把开发构建当成正式安装。
3. 创建或选择该 Bot 使用的 profile，记住返回的真实 ID；所有业务命令必须显式选择它。
4. 用户提供 TikHub API key 后，通过隐藏输入或安全的 stdin 配置，再执行免费的凭据与接口连通检查；不把 key 写进聊天、命令参数或日志。
5. 分别报告版本、入口、profile ID、安装自检与带密钥验证结果。尚未收到作者链接和检查频率时，在此结束；安装本身不需要付费 API。

遇到失败时停止并报告错误码及失败步骤。不要自行写包装器、改安装文件、手工写库或切换供应商路径绕过错误。已有目录、旧实例、凭据和数据不删除、不自动导入。

## 首次安装

以下命令用于 **POSIX shell（sh/bash/zsh）**。需要 `curl`、`tar` 和可持续使用的 Python 3.12+。如果 `python3` 太旧，把 `PYTHON=python3` 改为机器上已有的 `python3.12` 或合适的绝对路径；缺少运行环境时先报告，不继续安装。

默认目录：

| 用途 | 位置 |
| --- | --- |
| 程序 | `~/.local/opt/social-lurker` |
| 数据与 profile | `~/.local/share/social-lurker` |
| 共享凭据 | `~/.config/social-lurker/credentials.json` |
| 命令入口 | `~/.local/opt/social-lurker/bin/social-lurker` |

v0.5.2 的默认目录与构建资产统一使用 `social-lurker`。新资产为 `social-lurker-VERSION.tar.gz` 与 `.manifest.json`。

已有安装不自动重命名或迁移。继续调用原安装位置的入口，它仍绑定原数据根；凭据用 `--credentials-file` 指定原文件。新程序能验证历史发行资产，但 v0.5.1 的旧升级器无法识别新资产名称，首次跨命名升级需使用经校验的新源码升级工具（见安装文档）。

安装器只接受空白安装/数据目录。已有这些目录时先查明来源；已有纯 CLI 请使用升级步骤，旧版本实例不要原地覆盖。

下面下载固定的不可变 v0.5.2 资产，**先校验，再解包和执行安装器**。无需 Git、GitHub CLI、uv、pip 或 sudo。

```sh
set -eu
PYTHON=python3
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "需要 Python 3.12+"; import fcntl'
command -v curl >/dev/null
command -v tar >/dev/null

SL_PACKAGE_DIR="$(mktemp -d)"
SL_RELEASE_URL='https://github.com/shishengkai/social-lurker/releases/download/v0.5.2'
curl --fail --location --retry 3 "$SL_RELEASE_URL/social-lurker-0.5.2.tar.gz" \
  --output "$SL_PACKAGE_DIR/social-lurker-0.5.2.tar.gz"
curl --fail --location --retry 3 "$SL_RELEASE_URL/social-lurker-0.5.2.manifest.json" \
  --output "$SL_PACKAGE_DIR/social-lurker-0.5.2.manifest.json"

curl --fail --location 'https://api.github.com/repos/shishengkai/social-lurker/releases/tags/v0.5.2' \
  --output "$SL_PACKAGE_DIR/release.json"
"$PYTHON" - "$SL_PACKAGE_DIR" <<'PY'
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
root = Path(sys.argv[1])
release = json.loads((root / "release.json").read_text())
assert release["tag_name"] == "v0.5.2" and release["immutable"] is True
assert release["draft"] is False and release["prerelease"] is False
base = "https://github.com/shishengkai/social-lurker/releases/download/v0.5.2/"
for name in ("social-lurker-0.5.2.tar.gz", "social-lurker-0.5.2.manifest.json"):
    matches = [a for a in release["assets"] if a["name"] == name]
    assert len(matches) == 1 and matches[0]["browser_download_url"] == base + name
    digest = matches[0]["digest"]
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest[7:], "SHA256 校验失败"
api = "https://api.github.com/repos/shishengkai/social-lurker"
path = "/git/ref/tags/v0.5.2"
for _ in range(5):
    request = urllib.request.Request(api + path, headers={"Accept":"application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        obj = json.load(response)["object"]
    assert re.fullmatch(r"[a-f0-9]{40}", obj["sha"])
    if obj["type"] == "commit":
        break
    assert obj["type"] == "tag"
    path = "/git/tags/" + obj["sha"]
else:
    raise SystemExit("无法解析正式 tag")
m = json.loads((root / "social-lurker-0.5.2.manifest.json").read_text())
assert (m["product"], m["distribution"], m["version"], m["source_state"], m["git_sha"]) == (
    "social-lurker", "cli", "0.5.2", "clean", obj["sha"])
print("正式不可变发行、资产摘要与精确 tag 校验通过")
PY

mkdir "$SL_PACKAGE_DIR/unpacked"
tar -xzf "$SL_PACKAGE_DIR/social-lurker-0.5.2.tar.gz" -C "$SL_PACKAGE_DIR/unpacked"
"$PYTHON" "$SL_PACKAGE_DIR/unpacked/install.py" \
  --package "$SL_PACKAGE_DIR/social-lurker-0.5.2.tar.gz" --install-root "$HOME/.local/opt/social-lurker" --data-root "$HOME/.local/share/social-lurker"

SL_CLI="$HOME/.local/opt/social-lurker/bin/social-lurker"
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --version
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --help
```

成功时安装器返回 `installed: true`，版本为 `0.5.2`。安装完成后始终使用上述 `SL_CLI` 入口，它绑定安装时选定的 Python 和数据根；不要删除该 Python 环境。临时下载目录不参与后续运行。

要使用其他目录，在安装命令末尾加 `--install-root /绝对路径/程序 --data-root /绝对路径/数据`，随后把 `SL_CLI` 指向所选程序目录下的 `bin/social-lurker`。凭据路径可通过命令前的全局参数 `--credentials-file /绝对路径/credentials.json` 指定。

## 创建 profile 与安装自检

profile 隔离关注列表和数据库。编号自动分配为 p0001、p0002…，显示标签不是 ID；没有默认空间或全局“当前空间”。新 Bot 通常创建自己的 profile，不复用其他 Bot 的空间。

```sh
SL_CLI="$HOME/.local/opt/social-lurker/bin/social-lurker"
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" profile list
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" profile create --label '自媒体'
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" profile list
```

从输出中读取 `profile_id`，保存为这个 Bot 的固定配置。下面的 `p0001` **必须替换为实际返回的 ID**，不能假定每台机器都相同。

```sh
SL_PROFILE_ID=p0001
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --profile "$SL_PROFILE_ID" list
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --profile "$SL_PROFILE_ID" check
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" config status
```

新建且无作者的 profile 执行 `check` 不访问 TikHub，无需密钥。安装自检应看到版本 0.5.2、正确 profile、空关注列表、成功的 `complete` 和 `summary.requests=0`。`config status` 只显示是否配置及来源，不输出 key。

## 可选：无密钥健康排查

官方 [health/check](https://docs.tikhub.io/237673542e0) 是公开存活探测，HTTP200 且 `status=ok` 表示该健康接口可达；它不检查密钥或业务依赖。遇到网络问题可用它辅助定位，**不能用健康成功替代下面的配置验证**。

## 配置 TikHub 凭据

用户在终端输入时：

```sh
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" config set-key
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" config status
```

`set-key` 隐藏输入，原子保存凭据文件，权限为 `0600`。没有交互终端的 agent 使用 `config set-key --stdin`，由宿主安全凭据通道向 stdin 提供一行 key；**不要把真实 key 写在命令文本、脚本、聊天或日志里**。没有安全输入通道时，等待用户配置。

凭据文件优先于 `TIKHUB_API_KEY` 环境变量；文件中配置项为空或无效时报错，不静默切换到环境变量。共享 key 跨 profile 使用时，外围安排错峰。

## 免费检查凭据和接口连通性

配置 key 后，请求官方 [账户信息接口](https://docs.tikhub.io/186826050e0)：`GET /api/v1/tikhub/user/get_user_info`。它要求 Bearer key，能返回 key 状态与账户信息。2026-10-07 实查官方 [端点定价接口](https://docs.tikhub.io/186826054e0) 的 `endpoint_cost=0.0`，因此适合免费验证配置，而不是消耗新账号赠送额度请求作品。

`config check` 复用程序凭据读取、固定 TikHub 地址和结构化输出：

```sh
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" config check
# 自定义凭据文件时，使用与业务命令相同的路径：
"$SL_CLI" --credentials-file '/path/to/credentials.json' config check
# 需要完整 JSON 时：
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --format json config check
```

不需要 profile。先校验本地凭据，再不带密钥查询当前定价；只有目标端点的数值 `endpoint_cost=0` 才发送一次带密钥账户请求。未知或非零价格返回 `ENDPOINT_NOT_FREE`，停止认证。账户检查至少间隔一秒，不自动重试（即使设置了 `--request-retries`），不跟随重定向、不查询作品、不输出密钥、邮箱、余额或账户原始响应。

成功的 result 包含：

```json
{"credential_check":"passed","credential_source":"file","endpoint_cost":0,"business_api_tested":false}
```

`credential_source` 也可能为 `environment`。调用者同时检查退出码 0 和唯一 complete 的 `status=ok`；成功时 `summary.requests=2`。401/403、超时、无效响应、未验证邮箱或 key/账户异常均失败；网关拦截用 `HTTP_BLOCKED` 表示，不能据此断定 key 无效。凭据文件为空或损坏不回退环境变量。

该检查证明当前凭据能通过账户接口认证，不证明抖音或视频号权限、余额足以支付作品请求或查询成功；正式业务验收仍需授权后的真实作品接口。定价响应外层可能有通用计费文案，以目标端点的数值价格为依据；服务端以后改变定价时，程序会停止认证请求。

## 添加作者与查询（会调用 TikHub）

收到用户的真实作者/作品链接，且获得业务调用授权后再执行。下面是命令形式，`LINK` 与 `AUTHOR_ID` 都是占位值，不直接运行。

```sh
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --profile "$SL_PROFILE_ID" add 'LINK'
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --profile "$SL_PROFILE_ID" list
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --profile "$SL_PROFILE_ID" check
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --profile "$SL_PROFILE_ID" --format json check
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --profile "$SL_PROFILE_ID" unfollow --platform douyin --author-id AUTHOR_ID
```

- 抖音支持 `/user/<作者ID>`、`/video/<作品ID>`、`/note/<作品ID>` 及可识别短链；作品链接用于确定所属作者。视频号支持 `https://weixin.qq.com/sph/...`，未知形式报错。
- `add` 只登记作者首页作为基线，不输出新增、不导入全部历史。重复 active add 幂等；重新关注仍登记当下首页。
- `check` 从首页开始。未入库的稳定作品 ID 都算新增，包括老发布时间的作品；非空页全新增且有下一页才续页，混合页完整处理后停止。
- 默认 JSONL，逐条刷新；最后有 `complete`。完整 JSON 使用 `--format json`。没有完成记录不能视为整轮成功；失败、部分失败和正常无新增要区分。
- 无 ack、队列、重放或跨运行续页恢复；允许错过。`unfollow` 保留历史。

全局参数必须放在子命令前。调度、展示、通知由 Bot/外围负责，程序不创建定时任务、不发送消息、不提供 skill 或 daemon。不在未获用户授权时自动添加作者、调用付费接口或启用调度。

## 已有纯 CLI：显式升级

如果命令入口已经存在，先确认版本和 profile，不运行首次安装器。以下常规 upgrade 命令适用于已支持新资产名的版本；v0.5.0/v0.5.1 首次升级按下一段进行：

```sh
SL_CLI="$HOME/.local/opt/social-lurker/bin/social-lurker"
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --version
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" profile list
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" upgrade check
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" upgrade apply --version 0.5.2
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" --version
"$SL_CLI" --credentials-file "$HOME/.config/social-lurker/credentials.json" profile list
```

**v0.5.0/v0.5.1 → v0.5.2：** 按上面的下载、摘要/tag 校验和解包步骤取得正式包，跳过安装器。设置原安装根，使用已验证包内的升级代码执行事务（不会更改原数据根或凭据）：

```sh
SL_EXISTING_ROOT="$HOME/.local/opt/social-lurker-cli"  # 改为实际原安装根
PYTHONPATH="$SL_PACKAGE_DIR/unpacked/src" "$PYTHON" - "$SL_EXISTING_ROOT" "$SL_PACKAGE_DIR/social-lurker-0.5.2.tar.gz" <<'PY'
import sys
from pathlib import Path
from social_lurker.locks import lock
from social_lurker.upgrade import apply_package
root = Path(sys.argv[1]).expanduser().resolve()
with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
    result = apply_package(root, Path(sys.argv[2]).resolve())
print(result)
PY
SL_CLI="$SL_EXISTING_ROOT/bin/social-lurker"
"$SL_CLI" --version
"$SL_CLI" profile list
# 继续用 --credentials-file 指定原凭据文件。
```

升级会验证正式不可变 Release、tag、manifest 与包摘要；保留 profile、数据、配置、旧程序版本和备份。0.5.0/0.5.1→0.5.2 不需要 schema 迁移。只有显式 upgrade 查询/应用软件版本，日常业务不自动更新；不保留 Star。旧 0.3.6 实例不是这个升级流程的输入，不迁移或清理它。

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

开发时显式指定独立安装/数据根。详见 [实现协议](docs/implementation.md)、[安装升级机制](docs/installation.md) 和 [验收记录](docs/validation.md)。规范来自关联 social-lurker-brain 的 0.5.0 开发文档；0.5.2 是其兼容补丁。

v0.5.2 发布前已通过 166 项本地测试、安装与合成升级冒烟。正式 CI/资产验收以发行页与 brain CURRENT 记录为准。公开抖音作者短链解析已实测；付费平台接口完整覆盖、Grok 宿主网关恢复与实际增量仍待使用验证。
