# 开发验证 · 2026-09-12

当前证据来自本地未提交的开发实现，Python 3.12.13、macOS arm64。开发验证不等于 Grok Bot 产品已通过验收。[脱敏结构化结果](validation.json)不含密钥、签名媒体 URL 或全文副本。

## 已验证

- 51 项 pytest 通过；Ruff 检查、Python 编译通过。
- 测试覆盖：五表约束、长平台 ID、多 Bot 隔离、两作者三作品三通知、历史数量确认及复用、冻结范围、分页让出、停止/恢复/删除、逐段全文覆盖、非法 edits、过期 token、超长通知、发送 unknown、回执校验、ASR 提交未知、每阶段重试、磁盘/下载限制、控制日志恢复、发布包 hash/越界/链接、最高 Release 不回退、迁移恢复与 Star 独立授权。
- 开发安装构造独立版本包和 venv，通过共享 launcher 从实例 settings 分派；未创建真实 Bot 或 routine。无密钥时 doctor 准确返回凭据缺失；通过结构化 stdin 配置本地开发凭据后，Python、ffmpeg/ffprobe、Node、解密资源摘要及 SQLite 自检全通过。空 tick 返回零待发消息；delivery.verified 仍为 false。
- 两平台真实媒体均经下载、ffprobe、MP3 转换和完整解码检查；视频号样本实际使用 Node/WASM 解密。

| 样本 | 源视频字节 | MP3 字节 | 音轨时长 |
|---|---:|---:|---:|
| 视频号 AI创变工坊，14991940714846493129 | 60,660,900 | 164,487 | 54.612 秒 |
| 抖音 夏鹏，7684230783506189604 | 61,824,227 | 2,360,831 | 786.740 秒 |

统一输出 MP3 / 16 kHz / 单声道 / 24 kbps，源音轨与输出时长在规定容差内；没有裁剪。

视频号清点取得 134 条唯一作品：前 8 页各 15 条，第 9 页 14 条，第 10 页为空。尾页 videos=[]、count=0、up_continue=0，但仍带 last_buffer；额外查询仍为空。适配器据此实现明确的空尾页组合；不能单凭非空页 up_continue=0 判结束。API 偶发返回只有 message/debug 字段的内部错误，按错误处理；一次成功不证明长期稳定。134 是这次可访问的元信息数量，不保证全部都可下载。

视频号按 object_id 取详情、作者对应和生成真实分享短链也通过。未编造本地阅读链接或未经验证的平台 URL。抖音仅有限分页验证，没有运行全部 1032 条历史清点或批量媒体处理。

对这条 54.612 秒视频号音频执行了**一次真实 fal-ai/whisper 收费提交**：CDN 上传、任务 ID 保存、查询、获取全文和 SQLite 原稿入库成功，291 个字符。没有自动重提；原稿入库后删除该样本本地媒体。真实原稿只在忽略的本地测试 SQLite 中，未复制进文档；Grok Bot 校对尚未执行。

上传显式请求 172800 秒保留参数。当前只证明上传和转写成功，**尚未观察 48 小时后远端输入对象过期**，不能声称已验证删除。

## 仍待验收

- Grok Bot 的稳定身份、持久目录、复制隔离、原生校对、原生消息 ID/读回和长消息容量。
- routine 持续执行、重启续跑、包括校对过程在内的主会话零痕迹和空轮不推送。
- 正式不可变 Release 出现后的真实跨版本升级；目前仅实现并测试校验和恢复，不存在已发布版本。
- 目标 Linux 架构与 GitHub CI（工作流已编写，尚未推送触发）。

目标环境步骤见 [Grok Bot 验收](../skills/social-lurker/references/acceptance.md)。本次没有发送作品通知、创建 routine、点 Star、提交、推送或发布软件。

## 可重跑验证

普通 pytest 不联网、不消耗业务 API 额度。`tools/validate_live.py` 明确区分 `--media` 与 `--paid-asr`，只有显式开关才转写；运行前选定样本和费用范围。凭据用 `--credentials-file` 指向本地忽略文件。已有 ASR 提交状态应查询原 job，不要直接重跑产生新任务。

相关协议来源：[TikHub 视频号分享链接](https://docs.tikhub.io/472974844e0)、[fal 队列](https://fal.ai/docs/documentation/model-apis/inference/queue)、[fal CDN](https://fal.ai/docs/documentation/model-apis/fal-cdn)、[fal 保留规则](https://fal.ai/docs/documentation/model-apis/media-expiration)。文档说明与本页实际验证范围分别看待。
