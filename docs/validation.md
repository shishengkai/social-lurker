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

## 0.5.1 补丁本地验收（2026-10-07）

132 项 pytest、ruff/格式、compileall、依赖锁和安装升级冒烟通过。公开抖音主页短链实测解析成功，供应商请求为零。新增 27 项回归覆盖作者/作品分类、跳转边界、凭据隔离、首页基线和网关错误。

升级冒烟目标 0.5.2/schema2 仅为离线合成 fixture。正式 0.5.1 仍使用 schema1，无数据迁移。Git/CI/发布包验证另见 0.5.1 Release 和 brain CURRENT；Grok 网关恢复、付费平台接口和实际增量尚待使用验证。

## 本地开发补充：config check（2026-10-07，尚未 Git 交付或发布）

分支 codex/config-check，基于 main 934b6eb。新增免费认证命令；软件版本暂保持 0.5.1，已有不可变发行包未改变。

- 162 项 pytest 通过（新增 30 项认证检查正反例）；ruff 检查/格式、git diff --check 通过。
- 临时安装及合成升级/迁移冒烟通过，零供应商请求；验证安装入口识别新命令且禁止带 profile。
- 另做一次新开发包的临时安装实测：config check 使用现有环境变量凭据，当前零价检查通过，带密钥账户认证成功；退出 0、唯一 complete.status=ok、requests=2（公开定价一次、认证一次），全部作者/页/作品计数为零。未写真实凭据文件或修改旧实例。
- 离线覆盖未知/非零/类型错误/目标不符定价阻止认证、文件凭据优先、无效本地凭据零请求、账户/key 状态异常、401/网关 403/429/500/超时/非 JSON、不自动重试、输出关闭停止后续请求、JSONL/JSON 和输出脱敏。
- 未做付费作品 API 或 Grok Bot 验收，未 commit/push/PR/tag/发布；上述本机结果不替代跨平台 CI 或正式包升级验收。docs/validation.json 保留此前发行前验收快照。

## 本地开发补充：统一命名（2026-10-07）

默认安装/数据/凭据目录、构建资产与 User-Agent 移除 -cli。166 项测试通过，新增默认路径及新/历史发行资产兼容验证；ruff/格式、diff 检查、临时安装/合成升级冒烟通过。现有正式 v0.5.1 包在临时 home 显式新目录安装，显式新凭据文件 set-key/status、profile 创建/空 check 通过，零供应商请求。历史验证 JSON 的包路径原样保留；当前仍为未提交/未发布改动，实际安装目录未迁移。

## v0.5.2 发布前验收

免费认证命令与名称更正合并为 0.5.2，schema1 不变。166 项离线测试及安装/合成升级冒烟通过；合成目标改为 0.5.3/schema2，不是已发布软件。本机认证检查此前已通过，但发布不自动操作 Grok 或付费接口。README 更新到正式包名、GitHub 资产摘要/精确 tag 校验，以及旧升级器首次跨命名升级事务。Git/CI/正式下载验收见 v0.5.2 发行页与 brain CURRENT。

## 使用者命名 profile：本地验收（尚未发布）

codex/named-profiles 基于正式 0.5.2。189 项 pytest、ruff/格式、diff 检查与临时安装/合成升级通过；自选名称、路径篡改/符号链接拒绝、重复创建、原样区分、缺参/未知名称、备份安全与中断恢复覆盖。没有 TikHub 请求。

额外使用公开正式 0.5.2 包的原安装器创建临时旧实例与 p0001，再升级到本地 0.5.3/schema1 fixture：升级期间原 registry1 字节及数据库不变，显式创建自选名称才转换 registry2，旧 ID/路径/数据库仍保留，新安装入口的中文/斜杠名称创建与空 check 通过。0.5.3 只是 fixture，未发布；未修改实际实例、凭据或 Grok，未提交/推送。

## v0.5.3 发布前验收

189 项测试、ruff/格式、安装/合成升级与正式旧包兼容通过；数据库/output schema1 保持，新 registry2 在创建时写入，旧 registry1 升级不改写。合成迁移目标为 0.5.4/schema2，非已发布软件。Git/CI/正式公开资产验收见 v0.5.3 发行说明和 brain CURRENT。

## 本机真实接口调试与响应中断处理（2026-10-07，未发布）

用户授权向 Grok Bot 获取公开作者 ID 并在本机调试。0.5.3 同一首页参数（count=20/max_cursor=0/sort_type=0/channel=normal）在本机读取飞天小麻花 21 条、学习失败主页样本 20 条、孙老养生 23 条，HTTP/root code=200、status_code=0，现有严格解析通过。三样本未复现 Grok 的 PAGE_INVALID；不同运行时间、网络及凭据来源不能作为相同环境，未确定历史故障根因，也未证明远端恢复。

总计 5 次真实 TikHub 请求：两作者首页、孙老作品详情与首页、首页中断后一次显式重测。其中孙老首次首页 chunked 响应发生 IncompleteRead，部分字节丢弃，不作为页面使用。实际计费以供应商账单为准，不把请求数当收费次数。

本地修复：捕获 HTTPException（包含 IncompleteRead、BadStatusLine），映射 NETWORK_ERROR；默认零自动重试，显式配置的有界网络重试保持。公开短链网络异常同样返回类型化错误。没有放宽页面/身份校验，没有写入本机或远端业务数据库。

193 项 pytest、ruff check/format、临时安装和合成升级冒烟通过，冒烟供应商请求为零。分支 codex/douyin-network-debug；未 commit/push/发布/部署，软件版本仍为 0.5.3。响应中断修复不是历史 PAGE_INVALID 的根因修复。

## v0.5.4 发布前验收（2026-10-19）

捕获响应中断和无效 HTTP 状态行为 NETWORK_ERROR，保留完整错误/完成记录和既有有界重试，不使用部分页面、不放宽身份或页面校验。增加 4 项回归；193 项测试、静态检查及临时安装/合成升级重新验收。合成迁移目标改为 0.5.5/schema2，与正式 0.5.4/schema1 区分。

清理本机 skills/、vendor/ 空目录及 .DS_Store；这些残留没有被 Git 跟踪，也从未包含在当前 CLI 包内。旧实例、.local 数据、凭据、备份和既有发行资产保留。Git/CI/公开资产与真实 0.5.3→0.5.4 升级验收以 v0.5.4 发行说明及 brain CURRENT 为准。此次不重新调用付费 API 或升级 Grok。

## 三平台 adapter 本地验收（2026-10-19，未发布0.6.0）

分支 `codex/more-platform-adapters`，基于正式0.5.4/main `911731e015ffb013b4c0263782ac781b79b437a6`。软件开发版本0.6.0、数据库schema2，输出schema1、registry2不变；正式版仍0.5.4。本节覆盖本地实现与验收，不代表 Git/CI/软件发布或 Grok 验收。

### 本地工程验证

240项 pytest 全通过；ruff检查/格式、uv锁检查、git diff --check通过。临时安装与合成0.6.1/schema3升级冒烟通过，常规测试/冒烟供应商请求0。新增用例覆盖三平台路由/响应身份/续页、公众号多图文ID与长链接冲突、B站联合投稿的详情证明与未知归属拒绝、YouTube两路分页/去重/一边失败整页不提交，以及schema1→2逐行保全和提交点前后恢复。

另以 SHA256 校验过的公开正式0.5.4包创建临时旧实例：原版升级器拒绝本次平台约束扩展并保持0.5.4；随后新版桥接事务成功升级到本地0.6.0。两个自选profile的authors/works/meta逐行一致，registry原字节、测试凭据摘要不变，integrity_check=ok、foreign_key_check为空，旧版本及两次事务备份保留；新入口报告0.6.0。没有修改实际安装。

### 用户授权的真实链接验证

使用当前正常 FileSecrets 和生产CLI/adapter调用链，默认1RPS、retries=0，临时profile/数据库；不保存认证头、密钥、供应商正文或媒体。

| 公开输入链接 | 识别结果 | 最后一轮 add |
| --- | --- | --- |
| https://mp.weixin.qq.com/s/TSNQKkRpN1qbKsT7BvzqIw | 量子位 / gh_114e76fd6e5d | exit0，24条基线，2请求 |
| https://www.bilibili.com/video/BV1gKJ86JEuP | 老番茄 / 546195 | exit0，18条本人主发基线，4请求；2条联合投稿经详情核实后排除 |
| https://youtu.be/dQw4w9WgXcQ | Rick Astley / UCuAXFkgsw1L7xaCfnd5JJOw | exit0，视频/Shorts合计78条基线，3请求 |

最后一轮 add 均只有result与complete，无work。随后同profile `check`：exit0、status=ok、scan_complete=true、authors_total/succeeded=3/3、failed/unchecked=0、pages_committed=3、new_works=0、requests=6，唯一complete。证明真实首页登记和随后无新增查询可运行，不把无新增验收当作真实新发布通知验收。

另限定前两页检查原生adapter的响应结构和游标：公众号24/28条（2请求），B站排除他人主发项后18/17条（7请求，含详情证明），YouTube两路合计78/78条（4请求）；下一游标发生变化。没有遍历全部历史，业务add仍只取首页。没有实测完整长链分页覆盖或等待作者发布新作。

本轮研究/探测/诊断/重跑累计57次TikHub请求：最初探测8，首轮CLI7，联合投稿诊断1，第二轮CLI6，定点诊断7，最终CLI15，前两页结构检查13。首轮发现并修正B站联合投稿归属问题；第二轮B站列表与YouTube详情各一次PAGE_INVALID，后续请求成功，没有放宽校验或添加自动循环重试。这些短时成功不证明上游长期稳定，未核算供应商实际扣费。

### 未验证及交付边界

未等待真实新发布；新增输出/混合页停止/详情冲突等由离线回归覆盖。未覆盖所有公众号、会员/私密/删除/地区限制、直播归档或所有短链形态；YouTube只查询普通视频与Shorts，日期不够精确时保持null。未运行本次分支Linux/Python3.13 CI、未操作Grok、未升级/迁移任何实际旧实例。未commit、push、创建PR、打tag或发布0.6.0。

## v0.6.0 发布准备

用户已授权发布及通知王思聪安排盯梢者升级。README正式安装目标更新为0.6.0，下载校验与首次安装分开；0.5.x升级必须从已校验包导入新工具，持独占锁并传正式expected manifest，全部profile纳入同一事务。同安装根只升级一次，不重建profile或改变凭据/日程。240项测试、静态/格式检查、临时安装与合成升级冒烟通过；公开正式资产安装/桥接验收和CI结果以发行记录为准。
