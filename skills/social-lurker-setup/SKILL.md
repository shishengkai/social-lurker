---
name: social-lurker-setup
description: 为当前 Grok Bot 安装、配置或修复盯梢者；用户给出项目地址即可引导完成，重复执行保留数据，新 Bot 使用独立实例。
---

# 安装与配置盯梢者

用户只需说“请安装并配置盯梢者：https://github.com/shishengkai/social-lurker”。本 skill 负责连续编排安装、凭据、绑定、消息验证和唯一 routine；程序处理依赖与本地状态。不要将内部命令、UUID 选择、逐项工程验收变成让用户搬运的任务清单。

只询问缺失且不能可靠取得的信息。沿用已给出的偏好和授权，普通配置默认 Asia/Shanghai、30 分钟、Whisper 和本 Bot 自身 LLM。凭据不会从其他 Bot 自动复制；用户明确要求复用时才将指定凭据复制到当前实例 .env。平台确实需要用户接管时说明具体步骤。

## 1. 识别本 Bot 与安装来源

1. 当前 Bot 持久指令已有 `SOCIAL_LURKER_INSTANCE_ID` 时复用；有平台稳定 Bot ID 时一并核对。复制得到的 Bot 必须新实例，不能因为复制的指令带旧 UUID 就沿用。
2. 新 Bot 能取得稳定平台 ID 时，传 `--platform-bot-id`，安装器据此生成稳定 UUID；不要把显示名称当稳定 ID。
3. 平台不提供稳定 ID 时，先生成 UUID 并通过平台原生方式保存到本 Bot 持久指令，再传 `--instance`。重复执行读取同一值。不能可靠保存绑定时只报告这个阻塞，不能按目录数量猜测，也不能每次生成新 UUID。
4. 根目录默认 `/workspace/social-lurker`；已有绑定使用原目录。依赖、程序可共享，数据按实例分开。
5. 从固定仓库读取 README 的当前安装通道。正式通道只用最高不可变稳定 Release；当前仓库明示 preview 时，在开始说明这是试用版后使用 preview。不能因正式 Release 校验失败自行退回开发版。
6. 新安装将仓库 clone 到临时目录，解析 HEAD 为精确 commit，使用这一快照完成本次安装；不可在过程中追逐变化的 main。代码要求干净，普通安装不能传 `--allow-working-tree`。安装前若有网络或仓库问题，直接指出。

## 2. 执行本地安装，自动续接

在取得的源码目录执行 `python3 install.py`，参数用结构化数组传递：

```text
--root /workspace/social-lurker
--channel preview 或 stable（以 README 明示通道为准）
--instance 已有或已持久保存的 UUID
--platform-bot-id 平台稳定 ID（能取得时才传）
```

bootstrap 只需 Python 3.9+ 的标准库，会检查并准备 Python 3.12+、Node 22+、ffmpeg/ffprobe 和 MP3 编码器。Grok 电脑连基础 Python 3.9 都没有时，报告不满足 bootstrap 前提，不让用户猜版本或盲目运行网上安装脚本。

缺依赖时程序在项目 runtime/tools 中准备环境，不改系统 Python、shell profile 或其他 Bot 的包。安装会核验已固定的依赖安装器摘要，运行依赖按 requirements-runtime.txt 的版本和 hash 安装。缺失依赖可能需要几分钟。

已有程序包会校验后复用。已有实例保留 settings、.env、数据库和绑定版本；重复安装不等于升级。中断使用相同 ID 重试，安装锁 BUSY 时稍后继续；不能用换 UUID、删除资料或换根目录绕开错误。同名版本内容不同、共享依赖损坏或入口冲突时报告具体原因，按影响范围修复。

取得安装输出后，读取其 `setup_skill` 和本实例绑定版本的 references 继续。安装输出是本地状态，不是 Grok Bot 完整安装成功凭证。

## 3. 连续完成配置

通过 launcher 调用 `setup`，payload 为 `{"action":"status"}`，命令 envelope 见 [协议](../social-lurker/references/protocol.md)。反复读取当前状态推进，已经完成的步骤不重做。不要让用户自己排序。

1. 保存并读回返回的 `binding_instructions` 到当前 Bot 的持久指令；共享的 skill 只保存按实例读取规则。
2. 使用平台实际提供的保存或导入能力，将 `skill_registrations` 中的三个无实例信息入口注册并为本 Bot 启用。完整 SKILL.md 与 references 保留在版本包内，由共享入口按当前 Bot 绑定读取。已有同名兼容入口复用；不把当前版本完整指令或当前实例值覆盖到跨 Bot 的共享入口。若已有不兼容入口先说明影响，原生入口不可用则报告具体缺口；不能假定复制文件即已在平台注册。
3. `credentials_missing` 非空时一次询问所缺 Key。优先安全输入入口；否则引导用户在电脑中填写返回的 `.env` 路径。使用 `config credentials` 的结构化 stdin 或直接编辑本实例文件，权限 600，不回显值。已有 Key 不重复索取。
4. 重新自检。缺少依赖或数据库异常时先修复并复验；缺少凭据不能宣称配置成功。
5. 首次原生消息测试向当前会话发送少量测试文字，验证完整正文、实际工具返回的消息 ID 与可取得的读回结果。依据实测设置 max_chars；未知保留 null，不能称无限长。只有通过后才 `config set` 设置 delivery.verified=true 并传 `native_delivery_test_passed:true`。已有当前 Bot 的有效证据可复用。
6. 用本 Bot 原生 routine 工具查找返回的 routine.name 或已记录 ID，已有则核对/更新，没有才创建；一个 Bot 仅一个盯梢 routine。使用返回的实例、频率、时区及 instructions，原生工具读回成功后再写 routine.verified_interval_minutes。不得仅凭 JSON 配置就声称调度已创建。
7. 执行一轮空工作测试；确认不发空轮消息。平台是否留下过程对话痕迹单独核实，不通过就准确报告。收费平台/ASR 的样本验证在告知计费并已有授权后执行；安装本身不偷偷添加作者或调用收费服务。

测试失败只续做失败步骤，不删除已完成的安装、凭据或资料。修改普通配置也按上述状态推进；更改频率时同步已有 routine，而非创建新任务。

## 4. 完成与交接

本地配置就绪、绑定与 skill 实际启用、通知和 routine 读回及试运行通过后才报告完成。简洁告诉用户默认检查间隔、数据位置及“现在可以发博主链接”，存在限制则明确说明。完整产品验收见 [验收](../social-lurker/references/acceptance.md)，不要把一次空轮测试称全部验收通过。

仅本次发生了首次安装并且上述安装配置全部成功时，按 [共用 Star 规则](../social-lurker-upgrader/references/star.md) 判定一次可选邀请。初装中断后继续属于同一次事件，依当前会话判定是否已邀请；普通配置修改、自检、已完成安装的重复检查及修复不触发 Star。
