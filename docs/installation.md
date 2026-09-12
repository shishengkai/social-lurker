# 安装与宿主核验

用户入口是一句自然语言加仓库链接。完整步骤在 [维护 skill](../skills/social-lurker-maintainer/SKILL.md)。Bot 负责选取已验证持久目录、调用安装器、读取检查结果、建立/核验原生调度和技能绑定；用户仅提供缺失信息。

## 安装器

根 install.py 可由 Python 3.9+ 启动，实际产品要求 Python 3.12+。先复用已有解释器；缺失时指定独立 `--python-root`。Linux x86_64/aarch64 自动准备 Python，使用旧安装入口中已固定版本和 SHA-256 的 micromamba 2.3.3 下载器，只安装 conda-forge Python 3.12；不改 shell profile、不卸载共享解释器。不支持的环境明确报告缺 Python。共享环境创建失败后保留目录，需核对修复，不盲目删除重建。

当前开发预览示意（这些命令由 Bot 执行，不要求用户粘贴）：

```sh
python3 install.py --instance /已验证持久目录/social-lurker/当前实例 --bot-id 真实BotID --allow-working-tree
```

未传 `--allow-working-tree` 时选择固定官方仓库最高不可变稳定 Release。当前源码中存在 0.3.0 并不代表该 Release 已发布。安装器不会代替用户创建 Git tag 或发布软件。

创建新实例先原子落下配置、空凭据文件和三表数据库，再通过临时版本包原子进入 app/version。相同实例重复安装保留配置与凭据并核验版本文件；不能以重新安装越过维护计划或切换软件版本。未知数据库、非空未知目录、不同 Bot ID、符号链接路径均拒绝覆盖。macOS 上 `/tmp`、`/var` 等别名应先由调用方解析为实际绝对目录；不能绕过程序的符号链接拒绝策略。

## 真实证据与前台模式

`setup check` 只检查本地事实，不发送测试消息，不创建 routine。未取得证据时 mode=foreground_only。`setup bind` 可登记真实宿主结果引用：

- host：持久目录、实例隔离、原生调度、强静默、单条图文、长度单位/上限。
- delivery_update_id：显式试发且已经登记实际 sent 回执的作品；只有满足这一事实程序才记录 delivery_verified_at。
- routine：使用 `routine plan` 的 binding_hash、active 状态；setup bind 校验它仍对应最新关注和日程。绑定新的 routine_id 后重新取 plan，再登记原生调度成功证据。
- sources：平台/渠道、适配版本、覆盖等级及真实样本引用。近期页验证不是全量覆盖承诺。

证据只写入 settings.host.evidence_ref，不增加新的状态文件。时区/日程变更会使旧原生调度验证失效，需重新同步。程序阻止未核验宿主后台运行和未核验平台自动数据请求。测试 fixture 不能用于绕过真实验证。

## 密钥和运行

唯一 key 为当前实例 `.env` 中 TIKHUB_API_KEY，文件权限 0600。每次数据命令重新读取，替换 key 不解除持久 API 故障门禁。用受保护输入或用户直接编辑，不经普通聊天。当前版本不需要第二个 AI/媒体 key。

每条调用使用安装器返回的 Python 绝对路径、实例 run.py 以及 --instance。参数通过 stdin JSON，必须 protocol=1，结果也为单个 JSON envelope。示例：

```json
{"protocol":1,"automatic":true}
```

将它传给 `poll`。后台随后最多领取 settings.limits.notifications_per_activation 个 `dispatch next`（也传 automatic:true），每个许可只调用一次实际宿主发送工具，再提交可信 `dispatch report`。无通知与可操作故障时不要向用户输出执行过程。宿主不支持这一行为时明确维持前台模式。

## 开发和真实验收

`tools/smoke_install.py` 只在临时目录安装两个新实例、重复安装并调用稳定入口，使用假 key，不联网，不发送消息。Python 缺失自动下载、TikHub 新版端到端、真实 Grok Bot 绑定/图文/静默尚需目标环境实测，不能用这个冒烟结果代替。
