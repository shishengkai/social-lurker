---
name: social-lurker
description: 在 Grok Bot 中管理作者关注、检查新发布动态，并用宿主工具逐作品交付通知。
---

# 盯梢者日常入口

只处理新发布动态。每条作品一条消息，含作者、发布时间、标题、原作品链接；列表顺带返回且可公开访问的封面可附图。不下载作品、不转写、不生成全文或摘要。没有新作品且没有需处理故障时保持安静。

## 实例和调用

先使用当前 Bot 已绑定的 Python 绝对路径、实例绝对目录和该目录 `run.py`。不猜 `/workspace` 持久性，不扫描其他实例，不把 API key 放进对话、命令参数或日志。缺失安装或绑定时转维护入口。

调用形态：`<python> <instance>/run.py --instance <instance> <command>`，参数通过 stdin JSON 传递，每次必须含 `"protocol":1`。解析单个 JSON envelope；`ok:false` 是失败，不向用户复制原始执行输出。下列示意中的标识须替换。

- `watch add`：`{"protocol":1,"source":"用户提供的分享文字或主页链接"}`；或明确 platform、author_id。先解析可信作者身份再添加。添加后从现在开始，不自动获取或通知历史最新一条。
- `watch source`：用户明确要求切换抖音渠道时传 watch_id、variant（normal/lite）；清当前扫描，保留去重账本，新渠道无人值守前需独立验证。
- `watch list`、`status`：只读当前实例。向用户展示作者名称和状态，不展开内部游标。
- `watch pause/resume/remove`：参数 watch_id。暂停/停止取消待发；在途发送可能已有结果，不能承诺撤回。恢复从当前时刻，不补暂停作品。移除后重新添加才重新建立首次一页边界。
- `watch purge`：明确对象且用户要求删除其资料时传 watch_id、confirmed:true。程序阻止删除活跃关注及未决送达事实。
- `watch test-latest`：仅用户明确要求试发，传 watch_id；之后走相同 dispatch 协议。已发、unknown、cancelled 项不强制重发。
- `poll`：前台请求传 protocol；指定作者可加 watch_id。后台例行传 automatic:true。返回值是内部检查结果，不直接当作对话消息发送。
- `metadata retry`：用户明确要求修复某条缺字段作品时传 update_id。仍缺少必要字段时显示待处理，不虚构时间或链接。
- `dispatch list`：可传 state（默认 unknown），用于核对作品及发送尝试。
- `dispatch skip-queued`：用户明确指定范围时传 update_ids 数组。不能用它清理 sending/unknown。
- `export watches/updates`：用户明确要求时传新文件绝对 path；updates 还需 watch_id 或 since/until（UTC 秒）限定范围。不会导出全文、凭据、签名游标或内部回执。

## 原生 routine 与静默

只有 `setup check` 证明当前宿主可用，才建立/启用当前实例专属原生 routine。通过 `routine plan` 取得目标日程与绑定摘要：默认 Asia/Shanghai 07、09、11、13、15、17、19、21、23 点。未证实强静默时说明“前台可用，后台静默待验证”，不得悄悄创建系统 cron、守护服务或推送服务。

routine 内部先 poll automatic:true，再按下节处理待发项。后台不作版本检查、Star 邀请、不生成“正在检查”“执行完成”等面向用户的过程消息；不能消除宿主固有痕迹时如实报告能力不足。未到允许时间不启动新的例行请求和发送；已经获得的真实回执随时登记。不为余项、重试或版本检查额外唤醒。

每次激活最多交付 settings.limits.notifications_per_activation（默认 20）条消息，作品与运维提醒合并计数。余项等下一次原生日间激活。用户前台明确要求才可额外检查/处理。新增、暂停、恢复、移除影响是否存在活跃关注时，读取 routine plan；用实际发现的宿主工具同步本实例调度，保留其他 Bot。结果用维护入口的 setup bind 登记，不凭工具名字猜能力。

## 严格交付

1. `dispatch next`（后台传 automatic:true）返回 null 就结束。初次显式试发可传 foreground_test:true；这只允许未验证宿主交付 reason=test 的作品。程序未核验宿主长度限制时必须先补真实证据。
2. 返回的 instance_id、kind、object_id、attempt_id、payload_hash 是本次发送许可。**仅使用 payload.text 和 payload.images 调用已验证的宿主发送工具，原样发送，不加前后缀、不分割、不改写、不跨 Bot。** 每个许可只调用一次发送。宿主返回限流时登记真实失败/未知结果并立即结束本轮交付，不再领取下一条，不新增唤醒。图文格式转换只能使用已验证的一条消息图文能力；图片不受支持时先按维护入口更新图片能力，再领取新许可，不能私改已固定消息。
3. 取得宿主实际返回的 message id 和发送时间才用 `dispatch report` 登记 result:sent、provider_message_id、sent_at（UTC 秒）及完整许可标识。不能把模型输出、进程退出码或自行编造 ID 当作送达证据。
4. 超时或不确定用 result:unknown，绝不自动重发。可信工具明确拒绝或对该 attempt 的可信查询证明未送达，才用 result:not_sent 和 evidence：type 为 provider_rejected 或 provider_lookup_not_delivered，reference 指向真实工具结果，attempt_id 与许可一致。
5. `dispatch resolve` 使用完全相同的回执格式，仅在取得新的核对证据时调用。暂停后或升级期间迟到回执仍提交；pending_registration 表示维护收件箱已接收，尚未登记为 sent，不重复发送。

## 前台任务完成后的维护

用户主动任务完成且未等待输入时，自动调用 `upgrade check`，参数 quiet:true；有明确有效新版才简短说明版本与更新摘要，询问是否升级。失败或无新版不提示。用户同意后转维护入口执行同一授权；不在后台 poll 中检查升级。不要在普通使用后邀请 Star。
