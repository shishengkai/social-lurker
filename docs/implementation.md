# 0.5.0 实现与输出协议

本地实现依据 brain 的 0.5.0 四章；没有沿用旧 R1 的消息、调度、门禁或数据库。目标 0.5.0，输出 schema_version=1；软件版本与输出 schema 独立。

| 模块 | 职责 |
| --- | --- |
| cli/output | 参数、结构化错误、JSONL flush、JSON spool、唯一 complete |
| profiles/config | 自动 ID、显式空间、SecretSource、文件优先 |
| db/service | SL50 新库、整页事务、基线、去重、unfollow/check |
| adapters/transport | 两平台元信息、固定 origin、1 RPS、超时、有限重试/429 |
| locks/package/upgrade | 安装/空间 OS 锁、摘要验证、备份、提交点、恢复 |

## 命令

```text
social-lurker --profile=自选名称 [GLOBAL_OPTIONS] profile create [--label=显示标签]
social-lurker [GLOBAL_OPTIONS] profile list
social-lurker [GLOBAL_OPTIONS] config set-key [--stdin]
social-lurker [GLOBAL_OPTIONS] config status
social-lurker [GLOBAL_OPTIONS] config check
social-lurker --profile PROFILE_ID [GLOBAL_OPTIONS] add LINK
social-lurker --profile PROFILE_ID [GLOBAL_OPTIONS] list [--all]
social-lurker --profile PROFILE_ID [GLOBAL_OPTIONS] unfollow --platform PLATFORM --author-id AUTHOR_ID
social-lurker --profile PROFILE_ID [GLOBAL_OPTIONS] check
social-lurker [GLOBAL_OPTIONS] upgrade check
social-lurker [GLOBAL_OPTIONS] upgrade apply --version VERSION
```

GLOBAL_OPTIONS：--format jsonl|json、--credentials-file、--install-root、--data-root、--rps、--timeout、--request-retries。选项放在命令前；profile list/config/upgrade 禁止 --profile；profile create 与业务必须指定。没有默认空间或自动编号，调用者自选名称。

## 记录

公共字段：schema_version、cli_version、run_id（UUID）、type、command、profile_id。时间 UTC RFC3339，所有输出 ID 为字符串。type 为 work/author_error/result/error/complete。参数无法识别命令时 command=null。

work 包含 platform/author_id/author_name/work_id/title/published_at/discovered_at/url/cover_url/kind/missing_fields。可选字段未知为 null；不从媒体地址构造原链接。必需身份冲突拒绝整页。首见时间不随再次查看改变，非空元信息可更新，空值不抹掉已知字段。

complete 包含 status（ok/partial/error）、scan_complete、summary。check 三类作者计数相加等于 authors_total。按有限规则完整结束才 succeeded；先输出 work 后页失败仍 failed。全局鉴权/额度/限流终止后续请求，剩余作者 unchecked。调用级错误优先 error；无作者为 ok/scan_complete=true；非 check 扫描计数为零、scan_complete=null。

完整 JSON 最后输出公共字段（不含 type）、records 数组与 completion；records 不含 complete。本次临时 spool 不属于业务归档，不在数据库保存消费状态。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功，含无新增/幂等 |
| 1 | 运行、全失败、DB 或升级错误 |
| 2 | 参数/profile 选择错误 |
| 3 | check 部分成功 |
| 4 | profile/安装忙 |
| 5 | 本地配置/凭据无效或缺失 |
| 130 / 143 / 141 | SIGINT / SIGTERM / 输出关闭 |

error/author_error 用固定脱敏消息，不输出 argv、供应商错误正文、认证头或签名媒体 URL。正常收尾恰好一个 complete；管道关闭/强杀允许无 complete。

## 数据与运行保护

每 profile 的 state.sqlite：meta(profile_id)、authors、works。application_id=0x534C3530、user_version=1；foreign_keys=ON、DELETE journal、FULL synchronous、busy_timeout=5000。拒绝陌生文件、旧库、错 profile 和高版本库，不迁移旧 R1。

安装锁→registry 短锁→profile 操作锁→短 SQLite 事务。网络不处于 SQLite 写事务内。add/check/unfollow 操作锁跨调用保持，list 读取已提交快照。同 profile 忙立即失败。SIGINT/SIGTERM 终止当前等待，未提交事务回滚；BrokenPipe 在下一请求前/输出时检测。已提交页保留，不重放。

socket I/O 默认 60 秒，不代表 DNS、总运行时间或宿主时限。网络/5xx 默认不重试，显式配置最多 2 次；429 解析 Retry-After 秒/日期，无效值保守 1 秒，最多一次退避、累计不超过 30 秒，重复/超长停止整轮请求。HTTPS 校验开启，API Authorization 仅发往固定 TikHub origin，不跟随 API 重定向。抖音短链先按允许域名最多检查 5 次 HTTP 跳转，仅读取 Location；已识别作者主页后调用资料接口，作品链接调用分享作品接口。不下载网页正文或媒体，不执行脚本，不向分享域名发送 Authorization/Cookie。公开跳转与 API 共用限速，requests 仍只统计供应商请求。

## 适配证据

2026-10-07 官方公开 Markdown/OpenAPI 可读取（普通 HTML 的部分视频号页仍不可访问）。请求契约与 fixtures 已交叉核对；fixtures 是按契约构造的离线输入，不是新抓取的真实响应。

| 平台/角色 | 官方来源 |
| --- | --- |
| 抖音列表 | https://docs.tikhub.io/186826223e0.md |
| 抖音分享解析/详情/资料 | https://docs.tikhub.io/186826220e0.md / https://docs.tikhub.io/186826219e0.md / https://docs.tikhub.io/186826222e0.md |
| 视频号列表/详情/原链接 | https://docs.tikhub.io/472974841e0.md / https://docs.tikhub.io/472974842e0.md / https://docs.tikhub.io/472974844e0.md |

抖音首页 max_cursor=0、count=20、sort_type=0、channel=normal；整页验证作者，可证实他人主发的合作项排除且不能授权续页，未知归属报错。详情路径最多一次，不切换 Web/raw 渠道。

视频号 raw=false 的 username/videos/count/up_continue/last_buffer。up_continue=0 的非空页也是尾页，不沿用旧推断。作品大整数经 Python 精确解析后转字符串；每条新作至多一次详情、一次原链接，不为封面发请求，不读媒体/解密字段。只支持已核对的微信 sph 分享短链；未知主页形式报 SOURCE_LINK_INVALID。

真实可见性、全部类型和字段变更仍需后续付费授权及实际使用观察。Windows 尚不支持；Linux/macOS 使用 fcntl，当前本机只验证 macOS。

## v0.5.1：v0.5.0 使用反馈修复

官方 API 请求增加浏览器 User-Agent 与 JSON Accept。非结构化 HTTP 403 返回 HTTP_BLOCKED（可重试、当前运行停止后续请求），不再把网关拦截误判为密钥无效；供应商明确 code=403 或 HTTP 401 仍为 AUTH_FAILED。错误输出不含上游正文。

作者首页短链按重定向后的 /share/user/ 身份进入 handler_user_profile，避免误入 fetch_one_video_by_share_url；作品短链保留原分享解析端点。拒绝外部跳转、HTTP、循环、未知路径和超限跳转；添加仍只登记首页，不续页、不输出新增。

本机公开作者短链解析成功，未读取密钥、未调用 TikHub。新增离线回归与临时安装/合成升级验证不代表 Grok 宿主网关恢复或真实增量验收通过。正式不可变 v0.5.0 包保留；补丁版本为 0.5.1，数据库 schema 仍为 1，无迁移。

## 免费凭据认证检查（v0.5.2）

`config check` 无 profile、无业务数据库操作。验证本地 SecretSource 后，GET 公开 `/tikhub/user/get_endpoint_info`，要求 data.endpoint_uri 精确匹配 `/api/v1/tikhub/user/get_user_info`，数值 endpoint_cost=0（不接受字符串或 bool）。否则 ENDPOINT_NOT_FREE，且不发送认证请求。随后 GET 账户接口，使用根级 api_key_data/user_data，校验 key 状态 1、账户启用/未禁用且邮箱已验证。账户元信息不进入输出。

成功 result 为 credential_check=passed、credential_source=file|environment、endpoint_cost=0、business_api_tested=false；complete 延用既有协议，requests=2，作者/页/作品计数均为零。认证检查无自动重试或 429 重试，间隔至少一秒；超时、认证、网关错误沿用 Transport 类型。输出关闭、中断仍停止后续请求。价格检查成功不证明作品端点权限或业务可用。现有 v0.5.1 正式包没有此命令。

## 使用者命名 profile（v0.5.3）

profile ID 是非空 UTF-8 字符串，最多 1024 字符，按原样区分；display label 可选，不影响 ID。未知 ID 不自动注册，相同 ID 创建幂等，显式冲突标签拒绝覆盖。宿主身份信息由调用者自己提供，程序不读取 Grok/Codex/WorkBuddy 身份变量。

新 registry schema_version=2，不含 next_id；新路径为 profiles/id-SHA256(UTF-8 ID)/state.sqlite。旧 registry1 只读加载仍严格验证原 next_id 与 pNNNN 路径；创建新名称时原子转换为 registry2，保留旧条目的原 ID、标签、路径，停止编号。旧数据库不改名/搬动，数据库 schema 和输出 schema 均仍为 1。新 registry2 不能交给旧程序读取。

升级备份名采用已验证数据库目录名，避免把任意 ID 拼入路径，同时保留旧 pNNNN.sqlite 计划恢复兼容。创建与升级受原有 OS 锁保护；registry 损坏、路径篡改或符号链接拒绝猜测恢复。大小写、空白、Unicode 组合形式不同会生成不同空间。README 说明推荐 Bot/agent 使用永久唯一身份 ID，同名空间不提供所有权隔离。
