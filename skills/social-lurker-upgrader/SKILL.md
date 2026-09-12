---
name: social-lurker-upgrader
description: 检查盯梢者稳定版本，按用户一次授权执行受控升级及恢复；保留当前 Bot 数据和其他 Bot 的版本绑定。
---

# 升级盯梢者

只处理当前 Bot 已绑定的 social-lurker 实例。未安装或绑定不明，转 [安装配置](../social-lurker-setup/SKILL.md)。使用 [命令协议](../social-lurker/references/protocol.md)，每次携带当前实例与可取得的平台稳定 Bot ID。

## 检查版本

正常用户主动任务结束后可调用 `upgrade check`，automatic=true；用户主动询问版本也用此命令。后台 routine、待用户回答、任务未完成时均跳过，不新建检查更新的 routine。

只在 update 非空时展示当前版本、新版本和摘要。没有有效正式 Release、没有新版、网络或权限失败时，自动检查安静结束；用户主动查版本则如实解释。不能以 main、日期、预发布或较旧 Release 绕过最高正式版本验证。程序核对固定仓库、不可变 Release、精确 commit 和文件摘要。

## 执行升级

用户一次“升级”或等价请求即授权当前实例的标准稳定升级，无需再次确认相同事项。

1. `upgrade apply`，authorized=true。具体版本和 SHA 在 upgrade-plan 中冻结，重试不得改变目标。
2. 返回 draining 时按日常 skill 推进在途任务和原生校对；完成后对同一计划再次 apply。遵守调用时限，不建立另一套调度；维护不等于停止关注，不取消合法待发通知。
3. 程序准备独立版本包、备份本实例数据库与 settings、自检并切换当前实例的 runtime_version。保留 .env、正文和其他 Bot 的绑定。源码与发布清单各自固定 SHA。
4. 发生中断先查询维护状态，按 `upgrade recover` 的结果恢复；不要自行覆盖数据库或删除工作目录。业务已重新开放后不能拿旧备份覆盖新写入。
5. 共享入口不兼容、目标 schema 无迁移路径、破坏性变化或跨 Bot 影响时，具体说明超出标准范围的内容再决策。不能用升级当前 Bot 的授权替换其他 Bot 的程序或数据。
6. 成功后读取新绑定版本的三个 skill；通过 [setup](../social-lurker-setup/SKILL.md) 核对配置与原生入口仍有效，已有合格证据复用，失败只补对应步骤。真实执行完成、自检及必要的目标环境验证通过后报告升级成功。

本版只登记 schema 1 的路径；没有正式 Release 时不能宣称真实跨版本升级已验收。preview 安装不是稳定升级的替代方案。

## 可选 Star

实际升级全部成功后，按 [共用 Star 规则](references/star.md) 判定一次邀请；只检查版本、用户忽略更新或升级失败均不邀请。用户主动明确要求 Star 时，也按该规则核对账号和授权，但不凭普通任务确认代替 Star 授权。
