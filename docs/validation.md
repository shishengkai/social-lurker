# 0.5.0 本地验收结果（2026-10-07）

这是 Git 交付前的本地验收快照，软件版本 0.5.0，当时基准为旧 0.3.6 提交 30bdf9c33c0018f2a4910480e95c23f65f8d5c68。机器可读快照与当时开发包摘要见 validation.json。后续 Git/CI/正式资产验收见 [v0.5.0 Release](https://github.com/shishengkai/social-lurker/releases/tag/v0.5.0) 和关联 brain 的 CURRENT，下面的未执行项仅描述该快照时点。

| 层次 | 本次结果 |
| --- | --- |
| 规范 | 四章第二版，自动编号/必须显式选择及凭据目录收敛 |
| 内核与 CLI | 新 authors/works/meta、首页基线、整页新增、JSONL/JSON、错误计数与退出码 |
| 本地 pytest | 105 passed，macOS / Python 3.12.14；旧测试未计入 |
| 静态/构建 | ruff check、format、compileall、uv lock 检查通过；内容固定的 local-development 包构建完成 |
| 独立安装 | 临时中文/空格路径，可执行入口、profile/config、无作者查询通过 |
| 升级 | 离线假 Release 与合成版本；两 profile 备份/迁移、九阶段故障、提交点前回退/后向前、损坏计划/备份拒绝通过 |
| Git/发布 | 未暂存、commit/push、PR/合并、tag、正式 Release 或部署 |
| 实际使用 | 未操作 Grok；未对旧实例安装、迁移、停用或清理 |

子进程测试证明：work 在进程结束前逐条可读；SIGINT=130、SIGTERM=143、管道关闭=141，停止后续请求，已提交作品保留。多进程同 profile 互斥、不同 profile 独立；业务共享安装锁阻止升级。JSON 通过临时 spool 保序，正常完整收尾恰好一次 complete。

查询测试覆盖只登记首页、重复 add/unfollow、重新关注基线、老日期/新 ID、首条旧后条新、整页重复、无 ID/作者冲突整页拒绝、非空全新续页/混合页停止、空值保留元信息、后页失败后下次不恢复。失败、部分失败、全局停止和空结果分开计数。

安装器对合成旧目录的拒绝及摘要保全测试通过；包遍历/符号链接/重复/额外成员/摘要不符/旧 distribution 拒绝。候选检查在完整验证后运行。升级不重选失败候选、不覆盖旧版本；提交后新增事实保留，增加字段后写入仍通过。

fixtures 为公开契约构造的离线输入。程序的供应商请求均被替身替代，未调用付费 TikHub API，未读取真实凭据。合成 0.5.1/schema2 仅是测试，不代表存在已发布的新版本。

仍未验证：两平台付费 API 的实际可见性、更多类型/分页/字段；Grok stdout 实时暴露、截断、单次/总时限和进程生存；Linux/Python 3.13 与 GitHub CI；真实不可变 Release 的网络下载/实际升级；用户目标环境安装和实际使用。Windows 不在支持声明内。
