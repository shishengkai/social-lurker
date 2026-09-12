---
name: social-lurker-maintainer
description: 安装、核验 Grok Bot 宿主能力、修复接口、继续受控升级和卸载盯梢者轻量实例。
---

# 盯梢者维护入口

用户只需说“安装盯梢者”并给出 https://github.com/shishengkai/social-lurker 。由 Bot 完成以下动作；不要让用户重复粘贴长配置。数据独立于代码，不继承旧全文产品的数据库、依赖或配置。

## 安装

1. 发现当前 Bot 的执行环境、真实 Bot ID、持久可写目录与可用原生调度/发信工具。仅证实 `/workspace/social-lurker/` 属于该 Bot 持久环境时采用默认目录；实例为其中独立子目录。路径不明时才问用户。不得用桌面客户端路径推断 Bot 电脑路径。
2. 从固定官方仓库取得并审阅安装入口。安装器默认选择最高正式不可变稳定 Release、验证 tag 的精确 SHA 与软件清单/包摘要；无轻量稳定 Release 时报告待发布，不能悄悄安装旧全文版或 main。
3. 调用仓库 `install.py --instance <绝对实例目录> --bot-id <真实BotID>`。仅需 Python 3.12+；安装器可复用已有环境，缺失时由 Bot 提供独立、持久 `--python-root` 供安装器准备 Python。保留返回的解释器绝对路径。不需要任何媒体工具。用户明确使用当前开发预览时才加 `--allow-working-tree`，并明确这是预览。
4. Key 使用受保护输入写入该实例 `.env` 的 TIKHUB_API_KEY，或请用户直接编辑该文件。禁止普通聊天、shell 参数、日志输出 key。权限 0600，不拷贝其他 Bot 的配置。写入时遵守 state.lock 与维护门禁。
5. 运行稳定入口的 `setup check` 与 `config validate`。所有命令 stdin JSON 含 protocol:1。`setup bind` 只登记真实证据，不能为“通过验收”伪造时间、样本或消息 ID。

## 宿主证据与启用

settings.host.evidence_ref 保存有版本的证据引用对象（schema:1），不保存原始会话、密钥或完整服务响应。`setup bind` 接收 bot_id/routine_id、host、sources、evidence（真实工具结果引用）。

- host 的 durable_directory、instance_isolated、native_schedule_verified、quiet_execution_verified 是已验证事实；不是期待值。登记长度时必须一起提供 max_message_length、length_unit（unicode/utf8/utf16）和独立 length_evidence（工具约束/实测结果的真实引用），不能从普通消息发送成功猜测上限，不能任填 10000。此步在获取试发作品前完成；setup check 的 test_missing 会明确缺项。
- images_verified=true 必须同时提供 image_update_id（已 sent 且 payload 确有图片的试发作品）及 image_evidence（该消息封面实际显示的证据）。首次试发使用日常入口的 test_images:true，不能先谎报图文已验证再测试。图片失败可登记 images_verified=false，常规通知仍可纯文字发送；不得改写已发或 unknown 的作品重新试发。
- 若需证明发信能力，请用户明确要求“试发最近一条”，使用日常入口，收到实际回执后传 host.delivery_update_id。程序必须核对该试发项已 sent，才记录 delivery_verified_at。
- sources 键只支持 douyin:normal、douyin:lite、wechat_channels:default；每项含 adapter_version（当前 metadata-r1.2）、level 与 evidence。level 可为 unverified、recent_pages_verified、enumeration_verified、range_verified。分页内容、身份、边界必须有真实接口样本证据；本地 fixture 不可作为线上验证。没有范围/枚举证明，不能说全部作品都已覆盖。版本变化重新验证。
- 先用原生查询发现/创建本实例专属且暂停的 routine，绑定工具返回的真实 routine_id，不能把自拟名称当 ID。再取 routine plan：requested_active 是关注需求，active 是能力门禁后的目标，observed_active 是已登记实际状态。存在活跃关注但尚未验收时，active=false 是正常结果。原生操作之后必须再次查询，登记真实 routine_active、当前 binding_hash 及 evidence；宿主仍暂停就填 false，不能照抄期望值填 true。只有 prerequisites 齐备且原生实际启用后，setup check 才能返回 automatic_ready。维护恢复还传当前 routine_plan_id；用户期间暂停/修改关注时重新取计划。
- 按 setup check 给出能力边界：程序安装不等于背景强静默/稳定图文/真实回执已通过。缺关键宿主能力时保留前台模式。

## 配置与接口修复

`config set` 支持 timezone、monitor、rate_limit、limits 的受限变更；设置后重新验证/同步原生日程。程序已实现每实例统一 RPS 与并发 1、429 持久退避；不能把它说成账户全部 Bot 的共享限速。端点覆盖值只能更低，不配置每日请求硬上限。

凭据变更不清门禁。用户说“验证并恢复接口”时先 status 选取原失败 hold；`api verify-and-resume` 传 hold_id 与该已支持路径必要 params。程序只对本次 probe 临时豁免适用故障门禁，仍遵守维护、RPS 和 429。验证不代用户试发通知，不以其他路径成功冒充修复。禁止删除 api_holds_json 来强行恢复。

## 升级与中断恢复

用户明确要求升级即授权该次标准稳定升级，不再重复确认同一计划。`upgrade apply` 传 confirmed:true：固定官方最高有效版本/SHA，校验候选，停止新许可，等待在途结果，冻结备份，切换并登记提交点。没有新稳定版本、最高版验证失败不回退选择旧版本。

存在 maintenance.json 时用 `maintenance resume`，不重新选目标、不删除计划/锁/收件箱。prepared 等待发送回执时先登记真实结果；超时按 unknown 保留。冻结期间 `dispatch report` 可返回 pending_registration；只代表已入收件箱。

稳定入口会选中正确版本继续恢复。switching 中断恢复旧组合；committed 以后只完成新版本回执重放及原生 routine 恢复。routine_restore 返回时由 Bot 根据最新 routine plan 调整原生调度，然后 setup bind 登记真实本计划证据，再 maintenance resume。恢复失败不回滚已提交数据库、不关掉已重新开放的业务写入。不得覆盖用户维护后新增的暂停决定。

## 成功事件后的 Star 邀请

只有本次结果明确给出 star_event:install_completed 或 upgrade_completed 才调用 `star invite`（event 同值），同一事件一次。已 Star、身份未知、查询失败时安静跳过；不记用户长期偏好。不因拒绝或未回答影响安装/升级。

用户对这次邀请明确同意才 `star apply`，传邀请中的 account 和 confirmed:true；程序复核当前账号并读回结果。升级授权不是 Star 授权。普通运行、版本检查或回滚不发邀请。

## 卸载与清除

明确当前实例；暂停其关注，核对 sending/unknown 与未决运维提醒，停用本实例原生 routine 和技能绑定。维护计划未完成则先恢复。`uninstall` 传 confirmed:true、host_detached:true，默认只移除当前实例入口和版本代码，保留资料。`purge-instance` 仅明确删除全部实例资料的授权才调用，并传 backup_path（新绝对目录）或明确 discard_backup:true。默认阻止删除未决消息。只有用户另行明确放弃核对并删除时，先说明可能仍有在途消息，再在 purge-instance 加 abandon_unresolved:true；一般卸载或删除授权不能代替这一意图。绝不卸载共享 Python、删除其他 Bot 数据或清理旧全文资料。
