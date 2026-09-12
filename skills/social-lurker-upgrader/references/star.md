# 安装与升级共用的 Star 规则

固定仓库为 `shishengkai/social-lurker`。Star 与安装/升级分别授权，不影响已完成的核心操作。

- 只有首次安装配置全部成功或实际升级验证成功后，调用 `star invite`，event 分别为 install_completed / upgrade_completed；每次事件最多一次。已配置实例的重复安装、自检、修复、普通业务和后台 routine 不邀请。
- 缺 gh、缺认证、已 Star、查询失败时静默略过。不为此安装 gh、登录、扩权或索取 token。
- 邀请必须展示当前 GitHub 账号和固定仓库。用户直接对应邀请的“确认”或明确 Star 请求才是授权；安装、升级确认均不是 Star 授权。忽略不追问，本次拒绝即跳过。
- `star apply` 传当前会话明确授权的 account 和 confirmed=true；执行前后程序重新核对活动账号及 Star 状态。账号变化使原授权失效，先明确新的账号范围。
- 只有读回确认成功才报告成功；失败不自动重试，不回滚安装/升级。
- 账号、邀请是否已问及用户选择只留当前会话，不写 settings、数据库、业务日志、共享 skill 或项目文档。上下文不可靠就不执行。不能承诺 Grok Bot 自身会话不保留记录。
