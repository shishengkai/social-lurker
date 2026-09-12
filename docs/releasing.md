# 可复现发布与稳定升级

当前开发工作尚未提交或发布，本流程不代表已有稳定产物。

1. 完成相关本地测试与目标 Linux 架构验收，审查变更，单独授权后提交源代码。__version__ 和 pyproject.version 应与目标一致。
2. 从干净源码 commit 运行 `uv run python tools/build_release.py --version X.Y.Z --summary '更新摘要' --platform x86_64`。只有验收过的架构才加入 platform；不会根据开发 Mac 推断 Linux 已验证。
3. 得到 `dist/social-lurker-X.Y.Z.tar.gz` 和 `dist/release-manifest.json`。资产含文件摘要清单、固定源码 SHA、运行依赖 hash lock、app/skill/vendor。
4. 审查后把外置 release-manifest.json 提交到仓库根目录，再在这个**发布清单 commit** 上创建 vX.Y.Z。发布资产并启用不可变正式 Release。上述 Git/GitHub 动作分别需要用户授权。
5. 升级器取最高正式 SemVer，不允许无效时回退旧版本或 main；要求 immutable=true。它解析 tag→发布 commit→release-manifest.json，验证源码 commit 是其祖先，再比对资产整体摘要、包内 manifest 和每个文件摘要。

必须区分两个 SHA：资产 source_commit 固定待构建的源码；正式 tag 所指 commit 保存资产摘要。若要求清单同时含自身 Git commit SHA 和自己的包摘要，会产生无法构建的循环。这里不以 target_commitish、分支 HEAD 或日期替代任一边界。

升级只准备新目录、安装带 hash 的独立 Python 依赖，然后冻结当前实例新增工作，等待已有任务完成。SQLite 一致性备份、原 settings 与阶段日志只在 maintenance 临时保留。当前仅有 v1→v1 无结构变更；新结构迁移必须单独登记。目标程序 doctor 和数据库完整性通过后切换 runtime_version，重新开放前清理恢复文件。开放后的业务写入绝不拿旧快照自动覆盖。

标准升级不替换已有共享 launcher；协议不兼容会拒绝并要求明确跨 Bot 范围。其他实例继续使用原版。没有新正式发布时不能验收真实更新成功；开发安装另标 0.1.0-dev，不能混作 stable。
