# 实现说明

规范基线来自 social-lurker-brain Releases v0.2。本仓库实现本地程序和 Grok Bot skill；平台原生能力由目标环境验收，不伪造接口。

| 模块 | 责任 |
|---|---|
| config / util | 显式 UUID 目录、配置校验、无环境插值的凭据字典、原子写、文件锁和安全相对路径 |
| schema.sql / store | 五张表、外键与状态约束、短事务、来源归属、文本成果、意图和去重 |
| providers/tikhub | 视频号 v2 / 抖音 App V3，详情/账号/分页/分享链接；外部 ID 用字符串 |
| media / vendor | 限额流式下载、128 KiB 头部解密、MP3 16 kHz mono 24 kbps、完整解码与时长核对 |
| providers/fal | 独立实例凭据、显式 CDN 上传、单次队列提交、查询/取结果，输入保留请求 48 小时 |
| engine | 有界 tick、冻结范围、去重、续跑、故障与清理；无常驻服务 |
| proofread | 完整原稿、分段边界、owner/hash、局部 edits、最终全文原子入库 |
| notifications | 每作品完整正文、一次领取、停止周期检查、真实回执与 unknown 核对 |
| control / operations | 14 天控制意图、历史准备/确认、停止/恢复/删除/重试/跳过 |
| upgrade / star | 固定来源及摘要、独立版本包、维护备份/恢复；独立授权可选 Star |
| install.py / setup | 标准库安装引导、共享依赖准备、重复安装与版本复用；推导本 Bot 配置待办及无实例值的共享 skill 入口 |

0.2.0 将 agent 编排分为 social-lurker（日常）、social-lurker-setup（安装配置）、social-lurker-upgrader（升级）；共用 Star 规则置于 upgrader/references。三个 skill 使用同一个程序和当前 Bot 的五张表。原生平台注册、持久绑定、消息测试和 routine 操作由 setup skill 连续推进，本地命令仅提供真实状态和参数，不伪造平台完成记录。

额外技术字段：collection_runs.plan_confirmed 用于“只清点元信息”与“允许媒体处理”的明确区分；works.asr_submitted_at/asr_reviewed_at 区分实际提交和人工核对后的等待检查。它们是执行状态，不是配置副本，不增加业务表。

当前分页采取保守全范围清点：只有供应商明确尾页才完成枚举，并按发布时间选择最新/N 条。没有用未验证的排序假设提前截断；每次最多 100 页，后续从游标续扫。视频号非空列表的 up_continue=0 记 unknown；已实测尾页 videos=[]、count=0、up_continue=0（仍带游标），三个条件同时成立才结束。其他游标循环/尾页不可靠情况阻塞，不把主页数量等同于取全。此策略可能增加 TikHub 调用次数；日常扫描优化需先取得可靠排序/尾页证据。

各 Bot 一条可推进的作品占执行名额；已知 ASR 任务和校对交接继续占用。阻塞/失败/提交未知不无限卡住其他作品。共享 OS 锁限制下载/解密/转码；ASR 等待不占共享重处理锁。查询在 tick 的剩余预算内间隔 10–30 秒进行，预算不足留到已有 routine。

ASR 提交前提交 submitting 意图；响应丢失或任务 ID 入库前崩溃恢复为 submit_unknown。明确拒绝才允许有界重试。HTTP/服务错误只输出稳定码，不透传原始正文、签名 URL、异常字符串。上传对象实际过期仍须观察，提交保留参数不等于远端删除已证实。

校对 next/submit 使用数据库领取 token 和 600 秒租约。编辑结构校验只保证覆盖，不保证模型语义；不能用它宣称零错字。已保存原稿后清理媒体，校对失败不重复 ASR。运行期 settings 改动不更换已保存 ASR provider/model/task_id。

历史失败重试建立 selected 子批次，旧 collection_items 终态不回写。stop 更新 watch_epoch 并原子取消未启动 run 和待发意图；sending 已可能发出，转 unknown 待真实回执。已开始 run 按冻结范围继续。升级维护是单独状态，不通过 stop 实现。

首版只登记 schema=1。尚无生产旧库；遇未登记的未来 schema/migrate_from 明确拒绝，不假装通用迁移。正式新 schema 必须加入真实迁移和回滚测试才能发布。
