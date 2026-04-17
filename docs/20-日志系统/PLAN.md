# 20-日志系统 实现计划

## Context

项目迄今没有日志系统 —— 所有诊断都走 `print()` 直接打到 stdout/stderr，全项目共 8 个文件、约 64 处 print 调用。这个洞在后台自启场景尤其伤：

- **macOS LaunchAgent**：[autostart_macos.py:48-73](src/whisper_input/backends/autostart_macos.py#L48-L73) 生成的 plist 没有 `StandardErrorPath` / `StandardOutPath`，launchd 默认把这俩流丢进 `/dev/null`。自启模式下日志直接消失，用户报 bug 时唯一能做的是"从终端复现"。
- **Linux .desktop**：[whisper-input.desktop:10](src/whisper_input/assets/whisper-input.desktop#L10) 的 `Exec=whisper-input` 也没重定向，输出去向由 DE 决定。

目标是把日志系统一次性建起来：`structlog` + stdlib `logging` 做 backend，写到平台约定的日志目录，带文件轮转，plist 里加 stderr 重定向捕获 pre-logger 阶段的崩溃，设置页加一个"打开日志目录"的按钮。

用户偏好 `structlog` 而非纯 stdlib logging（BACKLOG 原话"现在没到那个阶段"被覆盖），理由是结构化日志在后续排查 / 可观测性上更好用。

## 决策清单（已和用户确认）

1. **文件渲染格式**：`structlog.processors.KeyValueRenderer`（logfmt）。stderr 仍用带色 `ConsoleRenderer`。
2. **`log_level` 加到 Web UI**：config 里加 `log_level` 字段，设置页一个下拉框。
3. **`app_bundle_macos.py` 的 19 个 print 拆两拨**：
   - `uninstall_cleanup()` 那 13 个保留 `print()` —— 永远是 `--uninstall` 这个显式 CLI 交互
   - `install_app_bundle()` 那 6 个走 logger —— 它从 `--init` *和* 启动时自动安装/升级重装路径都会触发（[__main__.py:283-287](src/whisper_input/__main__.py#L283-L287)），后者是用户从 `.app` 图标启动或 `uv tool upgrade` 后首次运行时的情景，**没有终端**，print 会丢
4. **`StandardErrorPath` 用独立文件** `whisper-input-launchd.log`，不经 Python 轮转 —— 避免 launchd 拿旧 fd 把 stderr 写进已被 RotatingFileHandler 重命名的孤儿文件。app 自己的结构化日志走 `whisper-input.log`。

## 日志目录

按现有 `config_manager.py:13-22` 的平台分支模式，在一个新模块 `whisper_input/logger.py` 里算出来：

- **macOS**：`~/Library/Logs/Whisper Input/`（Apple 推荐；Console.app 会自动扫这里）
- **Linux**：`$XDG_STATE_HOME/whisper-input/`，兜底 `~/.local/state/whisper-input/`
- **Dev 模式**：`{project_root}/logs/`（复用 `config_manager._find_project_root()`）

目录内文件：
- `whisper-input.log`：app 结构化日志（logfmt），RotatingFileHandler，`maxBytes=1_000_000`, `backupCount=3`
- `whisper-input-launchd.log`：macOS 专属，由 launchd 直接写入的 stderr / pre-logger 崩溃。**不轮转**（不是问题 —— pre-logger 崩溃极少，文件通常接近空）

## 模块设计

### 新建 `src/whisper_input/logger.py`

职责：
1. 解析日志目录 + 文件路径（暴露 `get_log_dir()`, `get_log_file()` 给设置页的"打开目录"按钮用）
2. `configure_logging(level: str)` 一次性配好 stdlib logging + structlog：
   - stdlib root logger 加两个 handler：`RotatingFileHandler`（走 logfmt processor chain）和 `StreamHandler(sys.stderr)`（走 ConsoleRenderer）
   - 用 `structlog.stdlib.ProcessorFormatter` 让两个 handler 共用一套 structlog processor 但末端用不同 renderer
   - processor chain：`merge_contextvars` → `add_logger_name` → `add_log_level` → `TimeStamper(fmt="iso")` → `StackInfoRenderer` → `format_exc_info` → `ProcessorFormatter.wrap_for_formatter`
3. 暴露 `get_logger(name)` 薄封装，调用方 `logger = get_logger(__name__)`

入口 [__main__.py:main()](src/whisper_input/__main__.py) 里**尽早**调 `configure_logging(config["log_level"])`，在任何其它 import / print 之前 —— 因为 config 要先加载才知道 level，所以顺序是：CLI args → config load → logger configure → 其余初始化。

### 修改 `src/whisper_input/config_manager.py`

- `DEFAULT_CONFIG` 加 `"log_level": "INFO"`（允许 `DEBUG/INFO/WARNING/ERROR`）
- 不改路径逻辑，日志目录完全由新 `logger.py` 负责

### 修改 `src/whisper_input/backends/autostart_macos.py`

在 `_build_plist()` 里加两个 key：

```xml
<key>StandardErrorPath</key>
<string>{LOG_DIR}/whisper-input-launchd.log</string>
<key>StandardOutPath</key>
<string>{LOG_DIR}/whisper-input-launchd.log</string>
```

`{LOG_DIR}` 从 `logger.get_log_dir()` 拿到并 XML 转义。两个流都指向同一文件 —— launchd 的写入是 append 模式，不同进程 / 流不会冲突。

`set_autostart()` 调用时顺手 `os.makedirs(log_dir, exist_ok=True)` —— 确保 launchd 启动时目录已存在（launchd 不会自己建）。

### 修改 `src/whisper_input/settings_server.py`

- `do_POST` 里加 `elif self.path == "/api/open-log-dir":` 分支，调 `_handle_open_log_dir()`：
  - macOS: `subprocess.Popen(["open", log_dir])`
  - Linux: `subprocess.Popen(["xdg-open", log_dir])`
  - 返回 `{"ok": True}` 或 `{"ok": False, "error": str(e)}`
- 模板已经有 `subprocess` import（探索确认过），无需新加

### 修改 `src/whisper_input/assets/settings.html`

- 在"高级"或"诊断"之类的分组里加一个 `打开日志目录` 按钮（照现有按钮样式）
- 加一个 `log_level` 下拉选项（`DEBUG / INFO / WARNING / ERROR`），和其它配置字段一样走 `GET /api/config` + `POST /api/config` 回路，改完需要重启生效（提示文案说明）
- 加上对应 i18n key：`settings.log_level`, `settings.open_log_dir`, `settings.log_level_restart_hint`
- i18n 文件改动：找到现有的 zh/en/fr 翻译位置补上（探索时未深挖，实施时跟代码看）

### print → logger 迁移清单

**保留 print（13 处）**：
- `app_bundle_macos.py` lines 245-330 内 `uninstall_cleanup()` 的所有 print

**迁移到 logger（~51 处）**：按探索 agent 给出的 level 分类：

| 文件 | 行 | level | event / 关键字段 |
|---|---|---|---|
| `__main__.py` | 125 | INFO | `event="recording_start"` |
| `__main__.py` | 138 | INFO | `event="recording_stop"` |
| `__main__.py` | 146 | WARNING | `event="no_audio"` |
| `__main__.py` | 160 | INFO | `event="transcription_complete", text_length=...` |
| `__main__.py` | 163 | WARNING | `event="no_text_recognized"` |
| `__main__.py` | 165 | ERROR | `logger.exception("recognize_failed")` |
| `__main__.py` | 175/183/191 | INFO | `event="config_toggle", key=..., enabled=...` |
| `__main__.py` | 197, 241-259 | INFO | 初始化各阶段 event |
| `__main__.py` | 308-313, 382-383 | INFO | 启动 banner / ready |
| `__main__.py` | 341, 391 | WARNING | `event="overlay_unavail"` / `event="tray_unavail"` |
| `__main__.py` | 367 | INFO | `event="shutting_down"` |
| `app_bundle_macos.py` | 127-172 | INFO | `install_app_bundle()` 6 处进度 |
| `hotkey_macos.py` | 46-57 | INFO / WARNING | 权限引导 |
| `hotkey_macos.py` | 143-146 | INFO | `event="hotkey_listening"` |
| `hotkey_macos.py` | 155-158 | ERROR | `logger.exception("hotkey_crashed")` |
| `hotkey_macos.py` | 159-160 | ERROR | `event="accessibility_denied"` |
| `hotkey_linux.py` | 44-46 | DEBUG | `event="keyboard_found", name=..., path=...` |
| `hotkey_linux.py` | 131-133 | ERROR | `event="no_keyboard"` |
| `hotkey_linux.py` | 136-139 | INFO | `event="hotkey_listening"` |
| `sense_voice.py` | 71, 84, 117-120 | INFO | 模型加载阶段 event |
| `recorder.py` | 60 | WARNING | `event="stream_status"` |
| `recorder.py` | 74-77 | DEBUG | `event="recording_stats", duration_ms=..., rms=...` |
| `overlay_macos.py` | 123 | ERROR | `logger.exception("main_thread_cb_failed")` |
| `settings_server.py` | 259-261 | INFO | `event="server_started", port=...` |

i18n 文案保留：event 名用英文 snake_case（给 logfmt 抓取），需要呈现给用户的消息仍走 `t()` 作为 `message=t("...")` 字段注入 structured log。

## 测试

新建 `tests/test_logger.py`：

1. **路径解析**：mock `IS_MACOS` / `XDG_STATE_HOME` / `_find_project_root`，验证三个场景下 `get_log_dir()` 返回预期路径
2. **configure_logging() 幂等**：调两次不应抛错或重复 handler
3. **RotatingFileHandler 触发**：tmp_path 作日志目录，`maxBytes=200`，连续写入触发一次轮转，验证 `.log.1` 文件出现
4. **logfmt 格式**：写入一条 `logger.info("test_event", foo="bar")`，读 log 文件，断言包含 `event=test_event foo=bar level=info`
5. **异常格式**：`try: raise ValueError("boom"); except: logger.exception("failed")`，断言日志里含 traceback

**手动验证**（代码改完跑）：
- `uv run ruff check .` + `uv run pytest`
- `uv run whisper-input` 跑一轮，确认 `~/Library/Logs/Whisper Input/whisper-input.log`（macOS）/ `~/.local/state/whisper-input/whisper-input.log`（Linux）出现并有内容
- 故意造错（断网 / 删模型文件）触发 exception 路径，验证 traceback 落盘
- `--init` 流程下 install_app_bundle() 的消息确认仍能在终端看见（通过 stderr ConsoleRenderer）
- 打开设置页点"打开日志目录"按钮，确认 Finder / 文件管理器弹出
- 设置页切 `log_level` 到 DEBUG，重启后验证 recorder 的统计 DEBUG 消息出现

## 非目标

- **不做 systemd-journald 双写**：Linux 用户可能习惯 `journalctl --user`，但双写增加复杂度，文件落盘已够
- **不做 log upload / 远程上报**
- **不做日志脱敏**：识别后的文本不入日志（只记 `text_length`）；音频永远不入
- **Linux .desktop `Exec=` 不加 shell 重定向**：`sh -c` 包装跨 DE 不稳定，依赖 app 自己的 logger 已足够

## 改动的关键文件

- 新增：[src/whisper_input/logger.py](src/whisper_input/logger.py)
- 新增：[tests/test_logger.py](tests/test_logger.py)
- 修改：[src/whisper_input/config_manager.py](src/whisper_input/config_manager.py)（加 `log_level` 默认值）
- 修改：[src/whisper_input/__main__.py](src/whisper_input/__main__.py)（尽早调 `configure_logging`；替换 26 处 print）
- 修改：[src/whisper_input/backends/autostart_macos.py](src/whisper_input/backends/autostart_macos.py)（plist 加 StandardErrorPath/StandardOutPath）
- 修改：[src/whisper_input/backends/app_bundle_macos.py](src/whisper_input/backends/app_bundle_macos.py)（install_app_bundle() 6 处 print → logger，其余保留）
- 修改：[src/whisper_input/backends/hotkey_macos.py](src/whisper_input/backends/hotkey_macos.py)（7 处）
- 修改：[src/whisper_input/backends/hotkey_linux.py](src/whisper_input/backends/hotkey_linux.py)（5 处）
- 修改：[src/whisper_input/backends/overlay_macos.py](src/whisper_input/backends/overlay_macos.py)（1 处 → logger.exception）
- 修改：[src/whisper_input/stt/sense_voice.py](src/whisper_input/stt/sense_voice.py)（3 处）
- 修改：[src/whisper_input/recorder.py](src/whisper_input/recorder.py)（2 处）
- 修改：[src/whisper_input/settings_server.py](src/whisper_input/settings_server.py)（1 处 print + 加 `/api/open-log-dir` 端点）
- 修改：[src/whisper_input/assets/settings.html](src/whisper_input/assets/settings.html)（log_level 下拉 + 打开日志目录按钮）
- 修改：i18n 翻译文件（补新 key）
- 修改：[pyproject.toml](pyproject.toml)（加 `structlog>=24.0`）
- 修改：[BACKLOG.md](BACKLOG.md)（删掉本轮完成的"日志系统"条目）
- 新增：`docs/20-日志系统/PLAN.md`（本计划拷贝进仓库，便于未来回溯）

## 风险 & 注意事项

1. **install_app_bundle() 在 `.app` 自动重装路径的 log 时序**：那会儿 logger 可能还没 configure（取决于是否在 `main()` 开头就 configure）。要确保 `configure_logging()` 在调 `install_app_bundle()` 之前就跑完。读代码后确认 __main__.py:262 的重定向路径，必要时在 `install_app_bundle()` 入口兜底一次 `configure_logging()`，idempotent。
2. **XML 转义 log 路径**：用户 `$HOME` 理论可能含 `&` 之类的字符，plist 里必须转义。已有 `_xml_escape` helper 复用。
3. **Dev 模式日志目录**：`logs/` 要加进根 `.gitignore`。
4. **替换 print 要一次做完**：PROMPT 明确要求不留"一半 print 一半 logger"的中间状态。实施时按文件顺序一把梳完，不要分多个 commit。
