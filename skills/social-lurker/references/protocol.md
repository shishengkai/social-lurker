# 本地命令协议

通用 envelope：`protocol_version:1`、唯一 `request_id`、`payload`。变更还需 `created_at`（UTC ISO8601 或 Unix 秒）；稳定 Bot ID 已绑定时每次传 `payload.platform_bot_id`。stdout 仅一个 JSON，业务阻塞退出码 0，请求无效 2，内部异常 1。

| command | payload |
|---|---|
| init | binding_confirmed:true，platform_bot_id 可空；无 --instance 时按本次 request_id 生成稳定 UUID，重装应传原 UUID |
| doctor | {}，不调用付费业务 API |
| config | action:get；set + settings 部分对象；credentials + values（仅 TIKHUB_API_KEY/FAL_KEY） |
| accounts | action:add + source + accept_service_costs:true；list；stop/resume/delete + account_id |
| collect | action:prepare + account_id + scope:count/count:N，或 time/amount:N/unit:hours\|days\|months\|years，或 all；已有历史需 mode:append\|replace |
| collect | action:status + run_id；confirm + run_id + acknowledged_count:N + accept_service_costs:true |
| tick | {}；有界推进，不直接发送消息 |
| status | {}；只返回状态，不返回所有文稿 |
| proofread | action:next/status，work_id 可选；submit 字段见校对规则 |
| notifications | action:list；claim + notification_id；render + notification_id + dispatch_token |
| notifications | action:ack + notification_id + dispatch_token + evidence:{provider_message_id,body_hash,readback_text?} |
| notifications | action:resolve + notification_id + resolution:unknown\|too_long\|sent\|resend；sent 需 evidence；resend 需 accept_duplicate_risk:true |
| retry | work_id，或 run_id；已结束历史需 run_id + work_ids；提交不明需 external_job_id 或明确 accept_duplicate_charge_risk:true |
| skip | run_id + work_id |
| upgrade | action:check + automatic 可选；apply + authorized:true；recover 用于中断的维护事务 |
| uninstall | {}，本地停止后仍需原生工具移除 routine/binding |
| star | action:invite + event:install_completed\|upgrade_completed；apply + account + confirmed:true，仅在独立授权后 |

结果中的 `pending_notification_ids` 和 `pending_proofread_work_ids` 是交接入口，next_action_at 是最早可继续时间，不承诺创建新唤醒。发送队列不存正文副本，render 从 SQLite 获取当前完整正文；不要将渲染输出写成常驻文件。

`delivery.verified` 默认 false；真实原生发送测试通过后才可 config set，附 `native_delivery_test_passed:true`。max_chars 以平台实测值配置，未知为 null；不是无限长承诺。routine.verified_interval_minutes 仅实际修改并读回调度后记录。
