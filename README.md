# 盯梢者（social-lurker）0.5.1

纯 Python CLI：登记作者首页基线，查询关注作者，把未入库作品的元信息逐条输出为 JSONL。调度、展示和通知由调用者承担。需要 Python 3.12+，运行依赖只有标准库。软件发行入口为 [v0.5.1](https://github.com/shishengkai/social-lurker/releases/tag/v0.5.1)，发布与真实平台/Grok 使用验收分别记账。

自动分配 p0001、p0002…，标签只用于显示。业务调用必须显式 --profile；没有默认空间或全局当前 profile。

## 本地开发

```sh
uv sync --frozen --group dev
uv run social-lurker --help
uv run pytest -q
uv run ruff check install.py src tests tools
uv run ruff format --check install.py src tests tools
uv run python tools/smoke_install.py
```

开发时用显式安装/数据根放置锁和数据；程序不读取旧 .env、旧库或旧 .local 实例。参考 [实现说明](docs/implementation.md) 与 [本地验收](docs/validation.md)。

## 安装与使用

正式包使用干净的固定提交构建，manifest 为 source_state=clean；发行资产与不可变 tag、Git SHA 和摘要一致。开发工作区预览须显式 --development，其 git_sha 只表示基准提交，实际代码由文件摘要固定。

```sh
uv run python tools/build_release.py --output dist/cli-0.5.1
uv run python install.py --package dist/cli-0.5.1/social-lurker-cli-0.5.1.tar.gz
~/.local/opt/social-lurker-cli/bin/social-lurker profile create --label=娱乐
~/.local/opt/social-lurker-cli/bin/social-lurker profile list
~/.local/opt/social-lurker-cli/bin/social-lurker config set-key
~/.local/opt/social-lurker-cli/bin/social-lurker --profile p0001 add 'https://www.douyin.com/user/AUTHOR_ID'
~/.local/opt/social-lurker-cli/bin/social-lurker --profile p0001 check
~/.local/opt/social-lurker-cli/bin/social-lurker --profile p0001 --format json list --all
~/.local/opt/social-lurker-cli/bin/social-lurker --profile p0001 unfollow --platform douyin --author-id AUTHOR_ID
```

业务示例会使用 TikHub 服务；自动测试和安装冒烟全部离线。config set-key 使用隐藏终端输入或 --stdin，不把 key 放在 argv。

默认安装根 ~/.local/opt/social-lurker-cli、数据根 ~/.local/share/social-lurker-cli、凭据 ~/.config/social-lurker-cli/credentials.json。安装时可指定 --install-root/--data-root；已安装入口绑定数据根，防止升级遗漏其他根的 profile。凭据路径可通过全局 --credentials-file 指定。全局选项放在命令前。详见 [安装与升级](docs/installation.md)。

## 查询与输出

- add 只登记首页，不输出 work，不导入全部历史。重复 active add 幂等；重新关注仍只登记当下首页。
- check 从首页开始，整页判断新旧、验证并提交后输出新增。非空页全新增且有下一页才继续；混合页完整处理后停止。
- 稳定作品 ID 未入库就算新增，即使发布时间很早。旧 ID 改题不输出，新 ID 重发输出。
- 无 ack、待消费队列、重放或跨运行游标恢复。入库到输出之间的损失由调用者承担；不能保证发现全部发布。
- 每条 JSONL flush，最后一条 complete；完整 JSON 使用临时 spool。无新增仍 complete，失败/部分失败与空结果区分。
- 同 profile 用 OS 操作锁；不同 profile 独立。各进程默认串行 1 RPS，429 有限等待；跨 profile 共用 key 由外围错峰。

命令、字段与退出码见 [CLI 协议](docs/implementation.md)。原链接未知可输出 null/missing_fields，不下载媒体、不产生正文。

只有显式 upgrade check / upgrade apply --version VERSION 查询或应用正式版本。日常业务不检查更新。没有 skill、调度、Bot 门禁、发送、Web、daemon 或 Star。

规范在关联 social-lurker-brain 的 Releases/04_CLI_0.5.0开发文档/。旧 0.3.6 源码可从历史提交 30bdf9c 追溯；旧实例、数据库和备份不因重写处置。两平台真实接口覆盖和 Grok 实际使用仍未验证。CI 及本地程序验收不能替代这些使用证据。
