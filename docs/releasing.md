# 轻量软件发布流程

当前代码 0.3.2。设计 R1 是规范版本，软件 Release 是独立交付动作。本次开发不创建 commit、push、tag 或 GitHub Release。

1. 完成本地检查和目标环境验收，核对仅有轻量三表/两个 skill。用户授权后提交并推送源码。
2. 在该精确、干净 commit 上运行 `python tools/build_release.py --output <仓库外绝对目录>`。生成 `social-lurker-0.3.2.tar.gz` 和 `release-manifest.json`。构建器不向仓库写入，不自动发布。
3. 由另行授权的发布流程创建指向同一 commit 的标准 vX.Y.Z tag。上传两个资产，核对下载包和清单摘要，并将正式 Release 锁定为 immutable；未锁定时安装器和升级器拒绝选用。不要在锁定后尝试覆盖资产。
4. 从安装器重新读取该正式 Release，核对包内代码版本、清单版本、tag、Git commit、各文件摘要、schema 与 authority 全部对应，再执行干净环境真实安装验收。

清单包含 authority=github.com/shishengkai/social-lurker、product=social-lurker-lightweight、version、channel=stable、protocol=1、schema_version=1、source_commit 和逐文件 SHA-256。外部 release-manifest.json 包含 manifest 对象与压缩包 SHA-256；包内 manifest.json 不包含自己的摘要，因此没有自引用 SHA 循环。归档内容排序、时间戳与 gzip mtime 固定，清单和包摘要可重建。

只打包标准库 Python 代码、schema.sql、两个 skill、稳定 run.py 与 LICENSE，不带数据库、.env、测试样本、日志、备份、第三方媒体二进制或虚拟环境。

发现逻辑排除 draft/prerelease/非标准 tag，按 SemVer 选最高正式版本；最高版本不可变性、清单或 commit 验证失败时停止，不回退到旧版。未来更改稳定 launcher 字节或 schema 时需要独立受控升级设计，当前版本拒绝未知迁移。正常任务自动检查失败安静结束，用户主动检查则报告原因。
