# 安装入口与恢复边界

用户只向 Grok Bot 提供仓库 URL 和安装请求。README 指向 setup skill；setup 负责平台交互，根 `install.py` 负责确定性本地安装。主 skill 路由至 setup/upgrader，业务流程不因拆分增加程序进程、数据库或调度服务。

## 安装器

bootstrap 需要 Python 3.9+、可访问固定来源的网络，以及用于验证源码快照的 Git；只使用 Python 标准库。正式通道为 `--channel stable`，明确试用为 `--channel preview`。默认 stable，不会因找不到正式版本或校验失败自动降级。当前 README 明示 preview；开发者测试修改中的源码才使用 `--allow-working-tree`。

`--instance UUID` 来自当前 Bot 已持久保存的绑定。能取得平台稳定 ID 时传 `--platform-bot-id`，未指定 UUID 则根据该 ID 确定性生成；复制 Bot 的新平台 ID 对应新 UUID。没有这两种可靠身份之一就阻塞，不猜测、不复用唯一现存目录。显示名不作身份。

优先检查现有 Python 3.12+、Node 22+、ffmpeg/ffprobe 和 libmp3lame，并保存持久解释器与工具绝对路径；例行调用恢复 PATH，不依赖交互式 shell。缺依赖时仅在 Linux x86_64/aarch64 的 runtime/tools 下创建独立工具环境，不修改系统 Python、shell profile 或服务。其他系统仅用于依赖已准备好的本地开发测试。

缺依赖的安装引导固定 micromamba 2.3.3-0 及 SHA-256，从 conda-forge 官方分发下载。环境使用 conda-forge 的 Python 3.12、Node 22、ffmpeg 7，解析后保存包版本、构建及摘要，再次运行复用已验证环境，不重新求解版本。Python 应用依赖继续使用 requirements-runtime.txt 的精确版本与 hash。micromamba 是依赖安装工具，无常驻进程，不调用 shell init。

来源：[micromamba 安装文档](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)、[固定版本分发元数据](https://api.anaconda.org/release/conda-forge/micromamba/2.3.3)。首次解析环境不等于跨时间已锁定的完整系统依赖集；已安装环境的具体包记录在 runtime/tools/dependencies.json。

## 复用与中断

- 同一实例：保留 settings、.env、SQLite 和 runtime_version，重新推导需要完成的配置步骤。安装不暗中升级。
- 新 Bot：只新建实例；同一版本代码与 venv 校验后共享，凭据默认空白。自动不跨实例复制 Key。
- 新版本包：先准备独立 `.installing` 目录，完成依赖安装和文件清单后原子发布。失败/中断只清理自己未发布的 staging，重试不触碰业务资料。
- 缺系统依赖的安装中断：通过 environment-pending.json 确认归属，重建尚未发布的临时依赖环境。已有有效共享环境损坏则报告，不自动删除其他 Bot 可能使用的环境。
- 已有版本内容不匹配、符号链接、共享 launcher 不兼容或实例绑定错误：保留原内容并阻塞。同名开发包不覆盖；更改源码包内容应提升版本或使用独立测试根目录。

开发入口 tools/install_dev.py 只是 bootstrap 的便利包装。tools/install_stable.py 为已准备环境后的底层稳定安装步骤。它们都不创建原生 routine、不发送消息、不替用户点 Star。

## 连续配置

安装后 `setup status` 返回缺失的 Key 名称、自检失败项、持久指令模板、三个不含实例值的共享 skill 注册入口和固定名称 `social-lurker:<UUID>` 的 routine 建议。平台保存短入口，完整技能保留在版本包中。实例 UUID、版本绑定和 Key 不写进共享 skill；每次按当前 Bot 持久绑定选择版本。routine 也按绑定版本读取，避免升级某一 Bot 时连带切换其他 Bot。

setup skill 通过真实原生工具保存/启用技能和本 Bot 绑定，完成通知测试，查找后创建或更新唯一 routine，读回后记录调度频率。程序输出的指令和设置不是原生工具调用成功的证据。平台自动启用 skill、持久绑定、消息回执、强静默及重启恢复仍需 Grok Bot 实测。
