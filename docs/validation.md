# 轻量 R1 验证记录

2026-09-13，0.3.3 / [f600198](https://github.com/shishengkai/social-lurker/commit/f600198fbfa16ac6dd123f3573d6a0f368a04155) 已推送，**105 项 pytest、ruff/格式与编译检查通过**，[Python 3.12/3.13 GitHub CI](https://github.com/shishengkai/social-lurker/actions/runs/34743357547) 成功。Bot 报告已完成 0.3.2 → 0.3.3 原地升级；当前待独立复核这次交付与新的视频号错误，完整业务验收尚未通过。下文将各版本和真实设备证据分别记录。

## 0.3.3 修复与白天续验（进行中）

2026-09-13 14:23，原 0.3.2 实例的一次稳定前台 poll 在约 3135 ms 内返回 pages=1、not_all_new、errors 为空，账本未新增作品；凌晨的超时本次未复现，不能推断其历史根因已修复。UTC 请求统计日切后，本次预留计数为 1。

随后抖音分享诊断约 3198 ms 返回 HTTP/code=200、status_code=0，aweme_details=null、filter_list.reason=5。TikHub [该端点官方说明](https://docs.tikhub.io/186826220e0)将 reason=5 解释为私密内容；这是接口的过滤结论，不据此断言作者账号私密。未新增关注、未试发作品，没有切换端点尝试获取受限内容。已请用户补充公开作品链接。

0.3.3 增加单作品响应解析，兼容 aweme_detail 对象及唯一的 aweme_details 元素；多作品或冲突表示拒绝猜测。空结果返回 DOUYIN_DETAIL_UNAVAILABLE，按已知过滤码解释问题，不泄露原响应，不增加请求，不自动重试。适配器版本 metadata-r1.3 使历史能力证据重新待验。105 项 pytest、ruff、格式与编译检查通过；新增用例覆盖过滤、两种单作品形状、身份匹配和歧义拒绝。单元素列表支持当前是代码与测试证据，真实公开抖音样本仍待核验。

手机镜像已连通；独立链接诊断 t22s0 已显示。直接在手机 Safari 访问视频号原链接后，可进入微信播放作者和标题匹配的作品。Grok Bot 消息内点击尚未成功核验；后续桌面点击也出现无响应，不能据此确定为产品链接故障。

同一原生任务的纯探针计划北京时间 14:40，独立读取成功 envelope 的 observed_at 为 14:43:46，probe_id=sl-phone-20260913-1440、data_requests=0、messages_sent=0、version=0.3.2。该次延迟 3 分 46 秒，证明一次真实原生唤醒，不保证精确时刻或完整自动业务。Bot 随后恢复正式任务定义和暂停，桌面侧栏已显示原名称及暂停；完整定义仍需最终复核。

14:51 Bot 报告 0.3.3 升级和 resume 完成，但随后的 AI创变工坊前台 poll 为 pages=0 / PAGE_IDENTITY_INVALID，新 metadata-r1.3 的视频号能力仍待验。随后直接核验实例 app/0.3.1、0.3.2、0.3.3 的全部文件集合和摘要均通过，0.3.3 源 SHA 为 f600198；SQLite integrity_check=ok，14 ignored、1 sent，维护文件不存在。升级前后逐行保全及凭据摘要一致由 Bot 的交付日志报告。

15:06 一次标准 Client/transport 诊断实际返回 HTTP/code=200，data 仅有 debug_id、debug_info、message；username、videos、objects、分页字段均不存在。供应商提示参数可能无效，不能仅凭该通用提示断言此前成功过的作者 ID 无效。耗时约 1030 ms，请求计数 4→5，未保存原响应；日志为 logs/wechat-list-structure-0.3.3.json。0.3.3 没有改变视频号解析逻辑，目前不能将上游间歇错误归因于升级。

0.3.4 针对此结构返回 WECHAT_LIST_UNAVAILABLE 和核对作者/接口的安全提示，保留真实身份冲突拒绝和有效空尾页处理，不增加请求或自动重试，也不透出供应商调试内容。恢复验证同样拒绝该错误信封。新增 3 项回归，全套 108 项测试、ruff/格式、编译与双实例安装冒烟通过；此处是本地修复证据，尚未代替新版本实例验收。

iPhone 通知设置已直接看到允许通知、立即推送、锁屏/通知中心/横幅开启，未修改系统设置。14:51 完成消息的系统横幅未被镜像捕捉，用户回答“没有留意”；因此推送通道和强静默仍未验证，quiet/native/images 能力保持未验证。没有把没有捕捉到横幅写成“没有推送”。

## 0.3.2 历史交付与验收

- 固定官方 commit 预览升级完成：0.3.1 → 0.3.2，维护归档结果为 done/upgraded，原生 routine 恢复为真实暂停状态。直接读取实例验证 manifest 源 SHA、新旧版本全部文件摘要、备份摘要及 SQLite integrity_check 均通过。
- 升级后的 watches 和 updates 与维护前备份逐行一致；原关注仍为「AI创变工坊」，14 条 ignored、1 条 sent。旧 app/0.3.1 保留。Bot 比较升级前后的 .env 摘要一致；独立检查其权限仍 0600、修改时间早于升级，未读取或输出密钥。
- 宿主长度实测消息 t10s1：从桌面实际收到的消息执行“复制”后独立计算，Unicode 2010、UTF-8 2028 字节、ASCII 2001，SHA256 为 e97d3aada4d49ab8bc371026169d1bc449d984becc7a331ebf754b7994f11ebd，与发送参数一致。已登记 measured / utf8 / 2000；2000 是实测范围内的保守操作上限，不是真实宿主最大长度。
- 正式日间 routine 仍暂停，实际 UI 已核对为 07/09/11/13/15/17/19/21/23 Asia/Shanghai，指令改为 routine poll → routine next，遇门禁不降级，并从 settings.app_version 选择当前 skill。setup check 尚缺原生调度与强静默证据，images_verified=false，模式 foreground_only。这个实例使用服务器 routine，UI 的“测试运行”按钮禁用；[官方说明](https://docs.x.ai/grok-bot/skills-routines-and-automations)提及该能力不代表当前实例可调用。
- 两平台完整试发尝试未通过：视频号分享解析两次 HTTP_TEMPORARY；抖音先 UPSTREAM_TEMPORARY，后 HTTP_TEMPORARY。未添加临时关注、未领取发送许可、未发送新作品，无法据此验收通知和回执完整链路。程序预留请求计数 5→9；预留次数不等于供应商实际收费次数。
- 对既有作者的独立前台 poll 也未通过：耗时 30.238 秒，envelope ok=true 但 pages=0、errors/stop_reason 含 HTTP_TEMPORARY，不能只凭 envelope 成功判断业务成功。该次计数 10→11；其间另有一次诊断预留，不计入前述两平台流程。
- 基础连通性另行通过：实例 curl 到 TikHub 根路径为 200，约 0.112 秒；Python TLS 连接约 0.059 秒。这些只证明基础连接，不证明数据端点可用。额外一次前台诊断保留 Client 门禁、锁、限速和计数，仅将本次整请求上限放宽到 90 秒：约 44.996 秒返回 HTTP 200，但未解析出 videos 列表，计数 11→12。诊断实现未重复包装 code/data；原始响应未保存，具体错误字段未知。标准约 30 秒请求仍失败，不能据此声称没有读超时，亦不能把延长上限作为已验证修复；具体 API 根因未完全确认。
- 临时诊断直接导入包时产生 9 个派生 .pyc，导致版本文件集合不匹配、稳定入口 ENTRY_STATE_INVALID。已核对均为已验证源码的缓存，将其可恢复地隔离到实例 logs/diagnostic-bytecode-20260913/，未改源码、manifest、数据库或凭据。随后直接调用完整 verify_directory，0.3.1 / 0.3.2 的文件集合与摘要均通过。
- 缓存隔离后，经稳定入口在真实夜间重跑并独立读回：routine poll 返回 quiet_hours / pages=0，routine next 返回 QUIET_HOURS；请求计数仍 12，updates 仍 ignored=14、sent=1，queued/sending/unknown=0，last_automatic_slot=null。该结果证明新版夜间门禁，不是原生唤醒、日间业务或手机推送静默的证据。
- 手机镜像尝试停在苹果要求“解锁 iPhone”的界面，无法代替用户完成设备验证。手机链接跳转与后台推送静默仍待真机核对；没有把桌面证据升级为手机验收通过。

实例证据为 logs/host-length-measurement.json、logs/e2e-preview-0.3.2.json 及升级备份中的 snapshot.json / maintenance-result.json。公开文档只保留脱敏结论，不包含凭据、原始响应或图片签名。

## 0.3.1 真实接口验证

- 用户提供的视频号分享链接在开发临时实例中完整执行：解析当前作者「凡诚Max」、获取 15 条作品、核对所有作品作者一致、取得最新作品发布时间和可用原分享链接；最后一次完整成功执行共 3 次 TikHub 请求。调试期间还有其他有界请求，此数不代表整轮调试总数或费用。
- 精简详情存在间歇性“HTTP/业务码 200，但 data 只有错误信息”；raw=true 能返回当前作品身份，已核验此回退，原始对象只在内存提取元信息。分享详情已有作者 ID/名称时不再重复查询资料接口；曾见额外资料请求失败，不将其误判为分享归属变化。
- 列表将 media.cover_url 与 cover_url_token 分开提供。未带签名的封面返回 400，限定腾讯图片域名组合后的 URL 返回 **200 / image/jpg**。仅检查响应头，未保存或处理图片正文。该结果证明图片地址可访问，不证明 Grok Bot 客户端已显示新卡片。
- 本次未验证分页完整覆盖、抖音当前接口、账户 RPS 例外、Grok Bot 静默或图文显示；没有把开发验证写成真实 Bot 的能力标记。

## Grok Bot 与 iPhone 实测

- 独立测试 Bot 实装 0.3.1 / 0a32f70，代码摘要与安装清单一致。真实试发 t0s26 已登记 sent，载荷含一张列表封面；桌面能看到图片，用户提供的 iPhone 截图却显示名为 stodownload 的普通文件，打开提示不能预览。
- 本实例既有封面一次 HEAD 返回 200、Content-Type=image/jpg，无 Content-Disposition，URL 路径末段为 stodownload；未下载正文。SendToUser 的 images 项仅有 url/alt，所见定义无 filename/MIME 字段。症状与手机文件分类相符，但未证明客户端具体分类算法。
- 用户授权的独立 Markdown 兼容性试验 t6s0 不传 images 数组；桌面仍显示图片，iPhone 截图只有“封面预览”替代文字。两种方式在目标手机均未通过，后续按既有纯文字回退规则执行，不改已发作品，不下载或托管图片。
- 错误的每日 04:05 验收任务在 04:14:35–04:14:54 实际执行：automatic 路径被 quiet_hours 阻止，随后旧指令降级到前台并扫描一页，累计请求计数由 4 变 5。自写 silent:true 和零作品发送不证明强静默，该轮不算合格自动验收。
- 已按授权将正式任务暂停，恢复北京时间 07–23 点每两小时的日程，删除门禁失败后改用前台的指令；实际 UI 已读回关闭状态与新指令。独立纯唤醒探针随后已运行，正式后台仍未启用。

- 日期限定的独立原生探针计划 04:33，实际文件写入时间为 04:42:46.911736（约延迟 9 分 47 秒）；原生 UI 有成功记录，probe_id 与实际临时任务一致。直接只读核对业务库，请求计数仍为 5，1 条 sent、14 条 ignored，last_automatic_slot=null；桌面对话没有本轮自动过程或结束消息。这是一次原生纯探针与桌面静默观察，不是完整业务或手机推送静默验收。手机侧反馈仍待确认。临时任务已暂停并删除，客户端短暂显示旧状态后，最终列表仅剩暂停的正式任务。
- 当前 0.3.1 配置已直接读回：images_verified=false，native_schedule_verified=false，quiet_execution_verified=false，routine_active=false；4000 unicode 的说明已改为用户选择的前台操作上限。未覆盖安装代码，未改作品账本或 .env。

## 固定 commit 预览升级入口

2026-09-13 按用户“commit & push 并交付实例、全程操作”的授权补齐 tools/upgrade_preview.py。入口验证固定官方远端、完整 commit、干净源码及实际构建包与 Git 文件一致；复用已安装版本的维护协调器和原实例稳定入口。专项测试覆盖拒绝错误身份/来源/脏源码/忽略文件注入、构建中途变更、凭据和 sent/unknown 账本保留、维护计划续接与真实子进程恢复。7 项专项通过，全套 96 项通过；真实 0.3.1 → 0.3.2 实例升级和暂停 routine 恢复也已完成，证据见上方。原生恢复时 routine_plan_id 必须取维护结果的外层 plan_id，不能取 routine_id 或 binding_hash。

## 已执行的本地验证

| 范围 | 证据 |
| --- | --- |
| 长度分类与后台探针 | tests/test_host_verification.py；用户选定值不放开自动模式、旧无类型/非法记录拒绝、整组证据更新、后台命令固定 automatic、探针无凭据/无网络/无业务状态变更且不自证静默 |
| Grok 实测回归 | tests/test_grok_regressions.py；精简详情一次回退/失败有界/身份冲突、签名域名约束与入库、长度预检早于 API、首次图文许可与显示证据、真实暂停登记和启用门禁、消息排版与注入转义 |
| 三表/实例隔离/时间槽 | tests/test_discovery.py；空实例无首次历史推送、独立库、日间槽、夜间相邻不判停机 |
| D3/D7 | 首次成功固定一页、失败不消费标记、混合页整页处理、全新增续页、续页恢复、逐作者共同旧槽判断、未来日期重新发现 |
| 元信息与请求 | tests/test_api.py；字段解析/大整数、仅列表封面、统一开始间隔、跨命令限速、UTC 统计、超过 1000 次不停止、30/120/600 秒退避层级与持久 429 |
| API 恢复互锁 | H1 路径权限 + 验证产生 H2 实例鉴权 + 原路径成功恢复，其他路径 H3 保留；错误结构不得误解除门禁 |
| 并发与锁 | 真实线程中的在途 HTTP 不阻塞暂停；旧代次请求/页面拒绝；独立进程争抢 state.lock 与锁顺序检查 |
| 交付 | tests/test_delivery.py；一作品一许可、排序、固定 payload、同代次资格、暂停后迟到 sent、超时 unknown 不自动重发、可信 not_sent、长度/封面及试发证据绑定 |
| 升级故障注入 | tests/test_lifecycle.py；prepared/frozen/备份/候选/switching/两文件切换/committed 各中断点；提交后异常不回滚；done 归档中断继续正确版本 |
| 回执与原生恢复交接 | 冻结收件箱去重、回放 DB 已提交但日志未标记后的幂等、回滚后迟到结果；原生恢复失败保留写入、尊重新的暂停；真实 routine 调用未执行 |
| 发布包与入口 | 构造 0.3.3 测试包、摘要/路径/符号链接拒绝、目标版本子进程自检、稳定入口重入和代码篡改阻断 |
| 本地维护 | tests/test_operations.py；配置拒绝与日程基准重设、旧调度证据拒绝、安全导出、库丢失不重建、无效维护门禁、卸载保留资料及明确放弃核对后的指定备份清除 |
| 自动升级/Star | tests/test_releases.py、test_star.py；最高 SemVer、immutable 和精确 tag SHA、不降级选版、异常静默检查、独立 Star 授权与账号复核；GitHub 操作均 mock |
| 安装 | tools/smoke_install.py；临时目录两个 Bot、重复安装不覆盖凭据、独立 ID、两个物理 skill、稳定入口可用；real_api_calls=0、host_messages=0 |

文件检查仅发现测试与冒烟中的明确假凭据，无真实 key。文档本地链接已检查；旧媒体/全文实现、依赖、技能和第三方解密资源已从当前源代码移除。未清理任何旧实例资料。

## 尚未通过目标环境验收

这些限制不是本地测试失败，也不能被本地测试替代：

- Grok Bot 的完整日间自动业务路径和无过程对话/推送的强静默。长度依据已经实测补齐；iPhone 两种封面发送方式均失败，当前采用文字回退。
- TikHub 抖音当前适配、视频号不同作品类型、分页/置顶/尾页/重复游标覆盖，以及实际 RPS 例外。单条分享解析与一页列表验证不等于整体覆盖，实例适配默认 unverified。
- 手机打开原链接效果、实际未知送达核对和宿主限流。普通试发的真实 message id/时间回执已有证据，不代表这些分支通过。
- 缺少 Python 时在目标 Linux 自动下载环境的完整安装；本次目标 Bot 复用已有 Python 3.13.5。
- 已发布的不可变轻量软件 Release 的下载、真实升级与原生 routine 恢复、成功后的 Star 邀请。当前软件 Release 尚未发布。
- 当前版本的完整真实业务验收：历史安装、升级恢复与长度证据已有直接验证；本次 0.3.3 CI 成功和 Bot 报告升级完成，不能代替两平台新增关注、通知交付和自动业务全部通过。

历史样本用于构建脱敏回归用例，当前真实接口结果单独记录在上方；没有把旧全文版的历史测试数或单页成功当作新版完整验收。

正式 V1–V23、A1–A19 仍应分别登记目标环境证据，不能因这里列出 105 项测试就宣称全套产品验收完成。
