---
name: social-lurker
description: 在 Grok Bot 中管理作者关注、检查新发布动态，并用宿主工具逐作品交付通知。
---

# 盯梢者日常入口

只处理新发布动态。每条作品一条消息，含作者、发布时间、标题、原作品链接；列表顺带返回且可公开访问的封面可附图。不下载作品、不转写、不生成全文或摘要。没有新作品且没有需处理故障时保持安静。

## 实例和调用

先使用当前 Bot 已绑定的 Python 绝对路径、实例绝对目录和该目录 `run.py`。不猜 `/workspace` 持久性，不扫描其他实例，不把 API key 放进对话、命令参数或日志。缺失安装或绑定时转维护入口。

调用形态：`<python> <instance>/run.py --instance <instance> <command>`，参数通过 stdin JSON 传递，每次必须含 `"protocol":1`。解析单个 JSON envelope；`ok:false` 是失败，不向用户复制原始执行输出。下列示意中的标识须替换。

- `watch add`：`{"protocol":1,"source":"用户提供的分享文字或主页链接"}`；或用户明确提供的 platform、author_id。先解析本次链接的可信作者身份再添加。解析失败时报告未添加，不能拿记忆中的旧作者 ID、昵称或短码替代本次链接并声称成功；只有用户明确指定该作者 ID 才可走独立入口。程序处理精简详情的有界原始详情回退，不在 shell 中另写接口重试。添加后从现在开始，不自动获取或通知历史最新一条。
- `watch source`：用户明确要求切换抖音渠道时传 watch_id、variant（normal/lite）；清当前扫描，保留去重账本，新渠道无人值守前需独立验证。
- `watch list`、`status`：只读当前实例。向用户展示作者名称和状态，不展开内部游标。
- `watch pause/resume/remove`：参数 watch_id。暂停/停止取消待发；在途发送可能已有结果，不能承诺撤回。恢复从当前时刻，不补暂停作品。移除后重新添加才重新建立首次一页边界。
- `watch purge`：明确对象且用户要求删除其资料时传 watch_id、confirmed:true。程序阻止删除活跃关注及未决送达事实。
- `watch test-latest`：仅用户明确要求试发；先读 `setup check`，处理 test_missing 中的长度证据/凭据缺项，再传 watch_id，避免获取作品后才发现无法发送。之后用返回的作品 id 走下方试发协议。已发、unknown、cancelled 项不强制重发；已发过的作品不能改数据库或伪造新 ID 重发来验收图片。
- `poll`：前台请求传 protocol；指定作者可加 watch_id。后台例行传 automatic:true。返回值是内部检查结果，不直接当作对话消息发送。
- `metadata retry`：用户明确要求修复某条缺字段作品时传 update_id。仍缺少必要字段时显示待处理，不虚构时间或链接。
- `dispatch list`：可传 state（默认 unknown），用于核对作品及发送尝试。
- `dispatch skip-queued`：用户明确指定范围时传 update_ids 数组。不能用它清理 sending/unknown。
- `export watches/updates`：用户明确要求时传新文件绝对 path；updates 还需 watch_id 或 since/until（UTC 秒）限定范围。不会导出全文、凭据、签名游标或内部回执。

## 原生 routine 与静默

安装时可创建暂停的专属原生 routine，以便核验工具能力。通过 `routine plan` 取得目标日程与绑定摘要：默认 Asia/Shanghai 07、09、11、13、15、17、19、21、23 点。active 是能力门禁后的目标状态，requested_active 仅说明存在活跃关注，observed_active 是最近登记的宿主实际状态。必须先完成 activation_missing，再按目标启用并查询真实结果；setup check 只有证据与状态同步才返回 automatic_ready。未证实强静默时保留前台模式，不创建系统 cron、守护服务或推送服务。

routine 内部先 poll automatic:true，再按下节处理待发项。后台不作版本检查、Star 邀请、不生成“正在检查”“执行完成”等面向用户的过程消息；不能消除宿主固有痕迹时如实报告能力不足。未到允许时间不启动新的例行请求和发送；已经获得的真实回执随时登记。不为余项、重试或版本检查额外唤醒。

每次激活最多交付 settings.limits.notifications_per_activation（默认 20）条消息，作品与运维提醒合并计数。余项等下一次原生日间激活。用户前台明确要求才可额外检查/处理。新增、暂停、恢复、移除影响是否存在活跃关注时，读取 routine plan；用实际发现的宿主工具同步本实例调度，保留其他 Bot。结果用维护入口的 setup bind 登记，不凭工具名字猜能力。

## 严格交付

1. `dispatch next`（后台传 automatic:true）返回 null 就结束。显式试发传 foreground_test:true 和 test-latest 返回的 update_id，程序仅领取该试发作品，不消费普通通知。用户要求的首次图文试发，在核对宿主工具支持一条消息携带图片参数后，再加 test_images:true；这只允许本次许可附列表封面，不将 images_verified 设为 true。TEST_COVER_UNAVAILABLE 表示尚未领取许可，可去掉 test_images 继续同作品纯文字试发；无需再调列表。若无合格封面，就如实报告图片尚未验收。程序未核验宿主长度限制时先补真实依据，不能随意填 10000 等数值。
2. 返回的 instance_id、kind、object_id、attempt_id、payload_hash 是本次发送许可。**仅使用 payload.text 和 payload.images 调用宿主发送工具，原样发送，不加前后缀、不分割、不改写、不跨 Bot。** 每个许可只调用一次发送。常规通知使用已验证能力；首次图文试发是明确的能力测试。封面与文字必须合在同一 message ID，不把图片单独补发；宿主支持原生 images 就映射该数组，只有工具明确支持同条 Markdown 图片时才使用等价格式转换。封面链接可能含仅访问该图片的短期签名，不得摘掉签名、在消息正文/日志中列出地址，或改为下载上传。宿主返回限流时登记真实失败/未知结果并结束本轮；已固定消息不能临时删图再发送。
3. 取得宿主实际返回的 message id 和发送时间才用 `dispatch report` 登记 result:sent、provider_message_id、sent_at（UTC 秒）及完整许可标识。不能把模型输出、进程退出码或自行编造 ID 当作送达证据。
4. 超时或不确定用 result:unknown，绝不自动重发。可信工具明确拒绝或对该 attempt 的可信查询证明未送达，才用 result:not_sent 和 evidence：type 为 provider_rejected 或 provider_lookup_not_delivered，reference 指向真实工具结果，attempt_id 与许可一致。
5. `dispatch resolve` 使用完全相同的回执格式，仅在取得新的核对证据时调用。暂停后或升级期间迟到回执仍提交；pending_registration 表示维护收件箱已接收，尚未登记为 sent，不重复发送。

图文试发 sent 后，还要实际核对封面与排版，再由维护入口登记 image_update_id 和 image_evidence；仅“发送工具返回成功”不足以证明图片已经显示。每次试发结束准确列出尚未核验项，不能直接声称后台可用。

## 前台任务完成后的维护

用户主动任务完成且未等待输入时，自动调用 `upgrade check`，参数 quiet:true；有明确有效新版才简短说明版本与更新摘要，询问是否升级。失败或无新版不提示。用户同意后转维护入口执行同一授权；不在后台 poll 中检查升级。不要在普通使用后邀请 Star。
