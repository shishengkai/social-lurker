# 安装与宿主核验

用户入口是一句自然语言加仓库链接。完整步骤在 [维护 skill](../skills/social-lurker-maintainer/SKILL.md)。Bot 负责选取已验证持久目录、调用安装器、读取检查结果、建立/核验原生调度和技能绑定；用户仅提供缺失信息。

## 安装器

根 install.py 可由 Python 3.9+ 启动，实际产品要求 Python 3.12+。先复用已有解释器；缺失时指定独立 `--python-root`。Linux x86_64/aarch64 自动准备 Python，使用旧安装入口中已固定版本和 SHA-256 的 micromamba 2.3.3 下载器，只安装 conda-forge Python 3.12；不改 shell profile、不卸载共享解释器。不支持的环境明确报告缺 Python。共享环境创建失败后保留目录，需核对修复，不盲目删除重建。

当前开发预览示意（这些命令由 Bot 执行，不要求用户粘贴）：

```sh
python3 install.py --instance /已验证持久目录/social-lurker/当前实例 --bot-id 真实BotID --allow-working-tree
```

未传 `--allow-working-tree` 时选择固定官方仓库最高不可变稳定 Release。当前源码中存在 0.3.2 并不代表该 Release 已发布。安装器不会代替用户创建 Git tag 或发布软件。

创建新实例先原子落下配置、空凭据文件和三表数据库，再通过临时版本包原子进入 app/version。相同实例重复安装保留配置与凭据并核验版本文件；不能以重新安装越过维护计划或切换软件版本。未知数据库、非空未知目录、不同 Bot ID、符号链接路径均拒绝覆盖。macOS 上 `/tmp`、`/var` 等别名应先由调用方解析为实际绝对目录；不能绕过程序的符号链接拒绝策略。

## 真实证据与前台模式

`setup check` 只检查本地事实，不发送测试消息，不创建 routine。未取得证据时 mode=foreground_only。`setup bind` 可登记真实宿主结果引用：

- host：持久目录、实例隔离、原生调度、强静默、单条图文、长度单位/上限。长度必须同时提供 max_message_length、length_unit、length_basis、length_evidence。basis 为 provider_documentation、measured 或 user_selected；最后一种只供前台试用，不算宿主长度证明。不能用一次短消息成功推断任意上限；setup check 的 test_ready/test_missing 和 message_length 在获取作品前指出缺项、依据与适用范围。
- delivery_update_id：显式试发且已经登记实际 sent 回执的作品；只有满足这一事实程序才记录 delivery_verified_at。
- images：首次明确图文试发使用 `dispatch next` 的 foreground_test:true、test_images:true、update_id，不提前标记图片能力。真实 sent 且已在用户阅读端检查显示后，用 image_update_id、image_evidence 登记 images_verified:true；纯文字回执或仅桌面显示不能证明 iPhone 可用。2026-09-13 iPhone 实测原生 images 为不可预览文件、Markdown 仅为替代文字，因此当前登记 false，后续常规通知用文字，不重发旧作品。
- routine：`routine plan` 的 requested_active 表示关注需求，active 是验收门禁后的启停目标，observed_active 是最近登记的实际状态。创建暂停任务后绑定真实 ID；按当前 binding_hash 登记原生查询结果。即使关注活跃，也允许如实登记暂停；能力未齐备不能登记已启用。synchronized=false 时继续同步，不把计划值冒充实际状态。
- sources：平台/渠道、适配版本、覆盖等级及真实样本引用。近期页验证不是全量覆盖承诺。

证据只写入 settings.host.evidence_ref，不增加新的状态文件。时区/日程变更会使旧原生调度验证失效，需重新同步。程序阻止未核验宿主后台运行和未核验平台自动数据请求。测试 fixture 不能用于绕过真实验证。

0.3.2 要求重新分类旧版本的长度记录；缺少 length_basis 时不能自动当成宿主实证。用户此前选择的 4000 unicode 可完整登记为 user_selected，后续有文档或长度实测再更新整组依据。平台适配版本为 metadata-r1.2，旧适配证据需重新核对。已经 sent/unknown 的作品不会因模板修改而重发；图文验收选尚未发送的作品。

`upgrade apply` 仍只接受正式不可变 Release。用户明确要求交付开发补丁时，使用仓库中的 `tools/upgrade_preview.py`：先从固定官方仓库取得干净 checkout，切到本次授权的完整 40 位 commit，再由当前实例的 Python 执行下面的命令。脚本检查远端、HEAD、工作区与包文件是否和该 commit 一致，调用实例已安装版本的维护协调器，经过排空、备份、候选核验、提交点和稳定入口恢复；不覆盖旧版本代码，不用重复安装切版本。

```sh
<当前实例Python> -B <固定commit源码>/tools/upgrade_preview.py --instance <当前实例绝对目录> --bot-id <真实BotID> --commit <完整40位commit> --confirm-preview
```

这是显式开发预览交付，不代表发布了稳定 Release，自动升级检查不会自动选择 main。包沿用既有清单格式，不能把其中 channel 字段当作 GitHub 正式发布证明；脚本结果明确标注 development_preview 和精确目标 commit。

返回 routine_restore 时，查询本实例原生任务并按最新 routine plan 同步，使用 setup bind 登记真实启停、binding_hash 和当前 routine_plan_id，再用稳定入口 maintenance resume。**routine_plan_id 取 maintenance resume / 预览升级结果外层的 plan_id，即当前维护计划 ID；binding_hash 取 routine plan 的当前结果。两者不能互相代替，也不能把 routine_id 当作维护计划 ID。** 新版本长度证据缺少类型或其他能力未验收时，恢复目标保持暂停。存在 maintenance.json 时不得再跑预览升级或改选目标，只从原实例 run.py 继续维护恢复。升级过程不写 .env、不清关注/发送记录，不自动启用尚未验收的后台。

## 密钥和运行

唯一 key 为当前实例 `.env` 中 TIKHUB_API_KEY，文件权限 0600。每次数据命令重新读取，替换 key 不解除持久 API 故障门禁。用受保护输入或用户直接编辑，不经普通聊天。当前版本不需要第二个 AI/媒体 key。

每条调用使用安装器返回的 Python 绝对路径、实例 run.py 以及 --instance。参数通过 stdin JSON，必须 protocol=1，结果也为单个 JSON envelope。示例：

```json
{"protocol":1}
```

0.3.2 将它传给 `routine poll`。后台随后最多领取 settings.limits.notifications_per_activation 个 `routine next`，二者固定 automatic 模式，拒绝传入降级参数。每个许可只调用一次实际宿主发送工具，再提交可信 `dispatch report`。任何门禁或夜间限制失败都停止，不换前台命令。0.3.1 仍用 poll / dispatch next 且必须 automatic:true；先核对安装版本，不能指示旧实例调用不存在的命令。无通知与可操作故障时不要向用户输出执行过程；宿主不支持时维持前台模式。

检查 poll 结果时还须读取 result.errors 和 scans 中的 stop_reason。envelope 的 ok=true 表示命令返回了结构化结果，不等于每个作者都检查成功；不能把 pages=0 且包含 HTTP_TEMPORARY 记为业务验收通过。

运维调用优先使用稳定 run.py。确需直接导入安装包做诊断时使用 Python 的 `-I -B`，禁止在 app/<version> 生成字节码、日志或临时文件。版本目录有额外文件会被完整性检查拒绝；先核对来源并修复，再重跑稳定入口，不能改用直接库写入绕过该拒绝。2026-09-13 的一次临时诊断产生 9 个 .pyc，隔离这些派生缓存后，两个版本的完整校验及稳定入口恢复正常。

## 不调用业务的宿主探针

0.3.2 的 `routine probe` 接收 `{"protocol":1,"probe_id":"本次唯一标识"}`，只读本实例并返回时间与标识，不调 TikHub、不领通知、不写 settings 或数据库。bound_routine_id 只是实例已绑定的正式任务，不代表本次真实触发任务；真实触发 ID 需从宿主核对。它的输出不是静默或调度验收证明，必须对照实际原生运行历史、工具执行和消息记录；前台执行一次也不证明原生唤醒。

真实工具支持一次性测试才使用该入口；当前 Grok Bot 未提供此动作。只能用 cron 时，按已授权的验收创建独立日期限定临时任务，设置唯一标识、有限有效时间窗和重复运行保护；正式任务保持暂停。运行完成或超时后停用并删除临时任务，查询确认。日期 cron 本身每年重复，不能当作一次性机制，也不能留下每天执行的核验任务。旧 0.3.1 可在同一边界下用标准库只写一次标记，但不能声称已经运行新版 probe 命令。

原生唤醒、无过程对话/通知、正确日间日程和实际业务路径分别取证。自写 silent:true 或无作品发送不证明强静默；能力标记只能按已观察事实登记。

## 开发和真实验收

`tools/smoke_install.py` 只在临时目录安装两个新实例、重复安装并调用稳定入口，使用假 key，不联网，不发送消息。Python 缺失自动下载、TikHub 新版端到端、真实 Grok Bot 绑定/图文/静默尚需目标环境实测，不能用这个冒烟结果代替。
