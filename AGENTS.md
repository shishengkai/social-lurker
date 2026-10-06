# social-lurker CLI implementation

当前软件版本 0.5.2；规范在关联 social-lurker-brain 的 Releases/04_CLI_0.5.0开发文档/。
先检查分支、工作区和适用指南。功能开发在 codex/ 分支进行，通过 PR 合回 main；发布从固定、干净的合并提交构建。

- 纯 Python 3.12+ 标准库 CLI；查询元信息。外围负责调度、展示、通知。
- 自动编号 p0001 起；业务必须显式 --profile，没有默认空间，显示名不是路径。
- add 仅首页基线；check 整页提交后输出，非空页全新增才续页，按平台与稳定作品 ID 去重。
- 无 ack、队列、重放、持久游标、Bot 门禁、skill、daemon、Star 或媒体处理。
- 文件凭据优先；只在文件未配置项时取环境。真实凭据不进源码、输出、测试和文档。
- 日常命令不查版本；只有显式 upgrade 操作软件。最高正式候选无效即失败，不回退。
- 历史源码保存在 Git；旧实例、数据库、备份、凭据和 .local 不导入、不清理。
- 本地测试：uv run pytest -q；静态检查：uv run ruff check/format --check；安装冒烟：uv run python tools/smoke_install.py。
- 测试与冒烟只用临时目录和离线 fixture，不调用 TikHub 或操作宿主。Grok 观察延至实际使用。
- 发布要求用户明确授权，按提交、PR/合并、CI、固定 SHA 包、不可变 Release 分别验收。发布不自动授权付费 API、Grok 操作、部署或旧实例处置。
