# 安装与显式升级

Python 3.12+、macOS/Linux。0.5.0 的 [软件发行页](https://github.com/shishengkai/social-lurker/releases/tag/v0.5.0) 提供固定 tag/SHA 的 CLI 包与 manifest，不安装进既有 0.3.6 实例。安装器只接受空白安装/数据目录，先拒绝旧目录再创建锁；不读取旧 settings/.env 或数据库。

默认安装 ~/.local/opt/social-lurker、数据 ~/.local/share/social-lurker、凭据 ~/.config/social-lurker/credentials.json。安装可传 --install-root/--data-root；稳定入口绑定安装时的数据根，全部 profile 都纳入升级。配置通过 --credentials-file 改路径。凭据 JSON 字段 tikhub_api_key；config set-key 隐藏输入或 --stdin 单行，原子写入 0600。文件配置项优先，未配置项才读 TIKHUB_API_KEY，空/损坏文件报错。

```text
INSTALL_ROOT/bin/social-lurker       可执行壳，锁定安装所用 Python
INSTALL_ROOT/bin/launcher.py         稳定引导程序
INSTALL_ROOT/versions/VERSION/       只读文件，manifest 与实际代码摘要
INSTALL_ROOT/current.json            原子选择版本，固定数据根
INSTALL_ROOT/install.lock            业务共享 / 升级独占
INSTALL_ROOT/upgrade-state.json      未完成安装事务计划
INSTALL_ROOT/backups/ID/             每 profile SQLite、registry/current、完成计划
DATA_ROOT/profiles.json              安装时建立空 registry；无默认 profile
DATA_ROOT/profiles/p0001/state.sqlite
DATA_ROOT/profiles/p0001/operation.lock
```

源码运行可用显式 --install-root/--data-root 放置自己的数据和锁；已安装入口不允许悄悄更换未纳入升级计划的数据根。软件与数据分离，显示标签不参与路径。

## 包与安装

```sh
uv run python tools/build_release.py --output dist/cli-0.5.0
uv run python install.py --package dist/cli-0.5.0/social-lurker-0.5.0.tar.gz --install-root /ABS/NEW_INSTALL --data-root /ABS/NEW_DATA
/ABS/NEW_INSTALL/bin/social-lurker profile create --label=娱乐
/ABS/NEW_INSTALL/bin/social-lurker config set-key
```

构建不提交、打 tag、创建 Release 或改变远端。正式构建要求干净工作区，git_sha 对应固定提交；--development 明确标 local-development，SHA 只是当前基准，真实内容由 files SHA256 固定。稳定升级拒绝 local-development 包。包包含 manifest、安装器、入口、代码、Python 要求、schema 范围；运行无外部包。安装器来自当前已审阅源码，不能先执行未验证候选的安装器。

解包拒绝绝对路径、路径穿越、符号链接、重复成员、清单外文件、不一致摘要和旧 distribution。完整验证后才运行候选自检，核对软件版本、schema 与 CLI 导入。入口每次验证当前版本的完整集合和摘要，禁止生成版本内字节码。安装选定的 Python 必须继续存在。

## 版本发现

upgrade check 只查询 shishengkai/social-lurker 正式 GitHub Releases。排除 draft/prerelease/非标准 tag，最高 SemVer 必须 immutable=true，tag 解析到精确 SHA，manifest product=social-lurker/distribution=cli，Python/schema 有效。最高候选无效返回 UPGRADE_FAILED，不回退；没有正式候选返回 latest_version=null/update_available=false。

check 下载并验证 manifest 摘要及包资产声明；apply --version VERSION 重新解析指定正式版本，下载包并验证实际摘要与全部文件，不二次确认、不附带 Star。业务命令不联网查更新。发行后的真实资产检查结果以 Release 说明和 brain 结果记录为准。迁移回归使用离线假 Release，不以合成版本声称完成对真实旧实例的升级。

## 迁移与恢复

升级持安装独占锁；业务持共享锁，忙立即失败。候选包不覆盖旧版本。先完成全部 profile 的 SQLite backup 与身份核验，再持久化 prepared/migrating/switching 计划。支持受控逐 schema 的增加字段/索引迁移，不默认支持删除数据或不兼容转换。每库迁移后核验 integrity_check/foreign_key_check，并执行已验证候选的兼容检查。

全部库通过后原子切换 current.json，作为唯一提交点。提交点前失败恢复全部验证过的备份；提交点后只向前核验归档，保留新事实。启动发现未完成计划时恢复同一已授权目标，不重新选择/下载版本。计划/备份损坏、指针不匹配时保持维护，业务不可写。旧版本与备份保留。

本地合成 0.5.1/schema2 用于测试机制，该编号仅为 fixture，没有发布或安装到实际实例。Grok 日程、通知及旧实例转换均在本轮范围之外。

## 目录与资产更名（v0.5.2）

新默认目录统一使用 social-lurker；不自动探测、移动、合并或删除原目录。已有入口绑定 current.json 中的原数据根，仍可使用，凭据通过 --credentials-file 显式指定原文件。源码调用也可用 --install-root/--data-root 指向已知安装。

新资产使用 social-lurker-VERSION.tar.gz/manifest.json；新升级器兼容 0.5.0/0.5.1 历史资产名。旧升级器不认识新资产，首次跨命名升级需从用户授权版本取得并校验新源码与正式包，在原安装根执行新代码的 apply_package（安装器不能覆盖非空目录）。示例在已审阅新源码根运行，PACKAGE 为已校验正式包、ROOT 为原安装根：

```sh
PYTHONPATH=src python3.12 - "$ROOT" "$PACKAGE" <<'PY'
import sys
from pathlib import Path
from social_lurker.locks import lock
from social_lurker.upgrade import apply_package
root = Path(sys.argv[1]).expanduser().resolve()
with lock(root / "install.lock", code="MAINTENANCE_BUSY"):
    apply_package(root, Path(sys.argv[2]).resolve())
PY
```

此工具沿用备份、全 profile 核验和原子切换，不改变原目录/数据根/凭据。只在用户授权升级且新正式包可用时执行；本次仅在临时目录验证。
