# 开发总结：日志系统

## 开发项背景

项目此前**完全没有日志系统** —— 64 处 `print()` 直接打到 stdout/stderr。手动从终端跑的时候能看见输出，自启场景下直接消失：

- **macOS LaunchAgent**：plist 没配 `StandardErrorPath` / `StandardOutPath`，launchd 把两个流都丢进 `/dev/null`
- **Linux XDG .desktop**：`Exec=whisper-input` 没重定向，输出去向由桌面环境决定

作为长期后台跑的工具，这是明显的洞 —— 用户报 bug 时唯一能做的是"从终端复现"，没有"看过去 24h 日志"这个选项。

## 实现方案

### 关键设计

1. **structlog 接 stdlib logging**（用户偏好，非 BACKLOG 原建议的"纯 stdlib logging"）。两端共用一套 processor chain，末端用不同 renderer：
   - stderr：`ConsoleRenderer`（TTY 带色、否则纯文本），保留原有终端 UX
   - 文件：`KeyValueRenderer`（logfmt），`event='foo' key='value'` 格式，grep 友好

2. **日志目录按平台分**，三分支逻辑复用 `config_manager._find_project_root()`：
   - Dev（探测到 `.git` + `pyproject.toml`）：`{repo}/logs/`
   - macOS：`~/Library/Logs/Whisper Input/`（Apple 推荐；Console.app 自动扫）
   - Linux：`$XDG_STATE_HOME/whisper-input/`，兜底 `~/.local/state/whisper-input/`

3. **轮转只管 app 自己的日志**。RotatingFileHandler 单文件 1 MB × 3 份 backup，磁盘上限 ~4 MB。

4. **launchd 的 stderr 走独立文件** `whisper-input-launchd.log`。**没按 PROMPT 原话"指向同一个文件"**，因为 RotatingFileHandler 改名时 launchd 持有的旧 fd 会把 stderr 写进已经被视作 `.log.1` 的孤儿文件。独立文件物理隔离、永不冲突。此文件只在 pre-logger 崩溃场景有内容，平常接近空，不轮转也无所谓。

5. **install_app_bundle 走 logger、uninstall_cleanup 保留 print**。前者从 `--init` **和**启动时自动安装/升级重装路径都能触发，后者只在 `--uninstall` 显式 CLI 里跑。前者的"没有终端"场景决定它必须入日志。

6. **configure_logging() 幂等**。`main()` 最早时机先以 INFO 起来（argparse 之后、config 加载之前），config 读入后再次调用覆盖为用户配置的 level —— handler 重新创建、root logger 重新挂，不会累加。

### 开发内容概括

- 新增 [src/whisper_input/logger.py](../../src/whisper_input/logger.py)：`get_log_dir()` / `get_log_file()` / `get_launchd_log_file()` / `configure_logging(level)` / `get_logger(name)`
- 51 处 `print()` → structlog（`__main__.py` 26 / `hotkey_macos.py` 7 / `install_app_bundle` 6 / `hotkey_linux.py` 5 / `sense_voice.py` 3 / `recorder.py` 2 / `overlay_macos.py` 1 / `settings_server.py` 1）。user-facing 消息保留 i18n 翻译，通过 `message=t("...")` 挂在结构化字段里
- 三处 `except … print(e)` → `logger.exception()`，自动带 traceback
- [autostart_macos.py:_build_plist](../../src/whisper_input/backends/autostart_macos.py) 加 `StandardErrorPath` + `StandardOutPath`；`set_autostart(True)` 顺手 mkdir 日志目录
- [settings_server.py](../../src/whisper_input/settings_server.py) 加 `/api/open-log-dir` 端点（macOS `open` / Linux `xdg-open`）
- [assets/settings.html](../../src/whisper_input/assets/settings.html) 加日志级别下拉框 + "打开日志目录"按钮，zh/en/fr 三份 i18n 补完
- `config.yaml` 加 `log_level: INFO` 默认值，`log_level` 进 `RESTART_KEYS`（改后提示重启）
- `.gitignore` 加 `logs/`

### 额外产物

- [tests/test_logger.py](../../tests/test_logger.py) 9 个用例：dev/macOS/Linux 三平台路径解析、XDG 兜底、configure_logging 幂等、logfmt 格式断言、rotation 触发（maxBytes=200 强制轮转）、exception traceback 入盘、launchd log 文件路径
- [tests/test_autostart_macos.py](../../tests/test_autostart_macos.py) 补 plist `StandardErrorPath` / `StandardOutPath` 字段断言，并加 `_mock_log_dir` helper 避免测试污染 repo 根目录
- 全套 96 用例通过（原 87 + 新增 9），ruff clean

## 局限性

1. **Linux .desktop `Exec=` 没加 shell 重定向**。`sh -c` 包装跨 DE 不稳定，依赖 app 自己 logger 的时序 —— 如果 app 在 logger configure 之前就崩，这段 stderr 在 Linux 自启场景仍会丢（GNOME 进 `journalctl --user`，其它 DE 行为各异）。macOS 有独立的 launchd log 文件兜底，Linux 没有对称方案。

2. **install_app_bundle 在 .app 自动重装路径的 log 时序**。`.app` 图标启动 → launcher dlopen Python → `main()` 起来 → configure_logging → install_app_bundle。这个链路上 logger 肯定先起，没问题。但如果未来改成"main 跑起来前就触发 install_app_bundle"，会出现 print/log 空窗。目前没这风险，但值得记住。

3. **日志文件里的结构化 event 仍然是"translated 文本混 snake_case"**。比如 `event='recording_start' message='开始录音...'` —— snake_case 是稳定 ID，可 grep；但用户看文件时会觉得 event 字段的英文短促不自然。要么在 ConsoleRenderer 的 stderr 用 message 作主显示（需要定制 renderer），要么接受现状。目前按 PROMPT 确认的方案走。

4. **手动实机验证还没跑全**：macOS `.app` 自启路径下 `~/Library/Logs/Whisper Input/` 是否按预期生成、plist 的 StandardErrorPath 能否真的捕获 launcher 崩溃的 stderr、设置页按钮在 Finder / Nautilus 里是否正常弹窗 —— dev 模式 smoke 验证过结构化输出和轮转逻辑，但完整自启链路要实机走一次。

## 后续 TODO

- **Linux 自启场景的 pre-logger 捕获**。如果这条洞在实机验证中被证实是痛点，可以在 `whisper-input.desktop` 里把 `Exec=` 改成 `sh -c "whisper-input 2>> ~/.local/state/whisper-input/whisper-input-desktop.log"` 之类，对称 macOS 的 launchd log。代价是 `sh -c` 在 KDE/XFCE 上的兼容性要测，而且路径无法硬编码（要同步 XDG_STATE_HOME）。
- **日志查看 UI**。目前按钮只能"在文件管理器里打开目录"。真的要排查 bug 时，用户可能希望设置页直接展示最近 N 行日志 + 一键复制功能。是 `GET /api/log-tail?lines=200` + Web UI 一段 `<pre>`，不难做但当前用户画像里没强需求。
- **日志上报/脱敏**：远程上报 + 识别文本自动脱敏，目前都不做。真要做 crash report upload 得先想清楚用户同意机制，scope 不小。
- **launchd log 文件的清理策略**：独立文件不轮转，理论上可能在极端崩溃循环下膨胀。加个"启动时若 > 10 MB 就 truncate"的兜底可以彻底防患，但极小概率事件，暂缓。
