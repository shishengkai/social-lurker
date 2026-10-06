# Git 交付与软件发布

功能改动在 codex/ 分支开发，通过 PR 合回 main，普通 merge 保留工程历史。发布须有用户明确授权。代码、Git 交付、CI、软件发行和实际使用分别记账，不能互相代替。

1. 核对适用指南、分支、工作区；按显式文件清单提交，不提交凭据、旧 .local 数据、虚拟环境、缓存或开发安装输出。
2. 推送分支、创建 PR，等待 ubuntu/macos × Python 3.12/3.13 的 pytest、ruff、安装冒烟与构建检查全部成功，再普通 merge。
3. 同步 main，核对远端/本地合并 SHA。执行 `uv run python tools/build_release.py --output <目录>`，禁止使用 --development 发布。
4. 在同一精确 SHA 创建 vX.Y.Z tag。正式 manifest 为 product=social-lurker、distribution=cli、source_state=clean，Python/schema 与逐文件 SHA256 有效。
5. 仓库须启用 immutable releases。先创建 draft，上传 social-lurker-cli-VERSION.tar.gz 与 social-lurker-cli-VERSION.manifest.json，复核上传摘要后发布，不在发布后补资产。
6. 核对 Release 非 draft/非 prerelease、immutable=true、tag 精确 SHA、GitHub 资产摘要与本地一致。下载正式资产，在独立临时目录安装并验证版本、profile/config 和显式 upgrade check/apply 无变化。
7. 将实际 PR、CI、Release、资产与未验证边界记录到 brain；不自动升级目标 Bot 或旧实例。

当前 v0.5.0 发布入口为 https://github.com/shishengkai/social-lurker/releases/tag/v0.5.0 。docs/validation.md/json 保存发布前的本地验收快照，后续结果以发行说明和 brain CURRENT 为准。

真实抖音/视频号接口及 Grok 使用验收仍需独立执行。合成 0.5.1/schema2 仅验证迁移机制，不是已发布版本；0.5.0 不迁移旧产品库。付费调用、部署、停止或清理旧实例不因发布自动授权。
