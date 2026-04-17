# 20-日志系统

## 背景

程序到目前为止**完全没有日志系统** —— 代码里一行 `logging` 都没用，所有输出都走 `print()` 打到 stdout/stderr。手动从终端起的时候还能看见输出，但只要走自启：

- **macOS 自启（LaunchAgent）**：[autostart_macos.py:33-58](src/whisper_input/backends/autostart_macos.py#L33-L58) 生成的 plist 没有 `StandardOutPath` / `StandardErrorPath`，launchd 默认把两个流丢到 `/dev/null`，**自启模式下日志直接消失**。
- **Linux 自启（XDG .desktop）**：[whisper-input.desktop](src/whisper_input/assets/whisper-input.desktop) 的 `Exec=whisper-input` 也没重定向，输出去向取决于桌面环境（GNOME 进 `journalctl --user`，其它 DE 行为各异）。

对一个长期跑在后台的工具，这是个明显的洞 —— 用户报 bug 时唯一能让他做的是"从终端手动复现"，完全没有"看一下过去 24h 日志"这个选项。

目前全项目共 8 个文件、64 处 `print()` 调用：`__main__.py` (26)、`app_bundle_macos.py` (19)、`hotkey_macos.py` (7)、`hotkey_linux.py` (5)、`sense_voice.py` (3)、`recorder.py` (2)、`overlay_macos.py` (1)、`settings_server.py` (1)。

## 目标

1. **统一日志框架**：用 `structlog`（用户偏好，结构化日志对后续做可观测性/ bug 排查更友好）替换掉所有 `print()` 调用，按 level 分流（DEBUG / INFO / WARNING / ERROR）。
2. **日志文件落盘**到平台约定目录：
   - macOS: `~/Library/Logs/whisper-input/whisper-input.log`（Apple 推荐位置，Console.app 会自动扫这里）
   - Linux: `$XDG_STATE_HOME/whisper-input/whisper-input.log`，兜底 `~/.local/state/whisper-input/whisper-input.log`（XDG 规范下 state 目录即放日志的地方）
3. **文件轮转**：单文件上限 1 MB，保留 3 轮，避免长期运行撑爆磁盘。
4. **LaunchAgent plist 的 `StandardErrorPath` 指向同一个文件**：这样在 logging 配置起来之前的阶段（launchd spawn 失败 / Python 解释器崩溃前的 traceback）也能被捕获。
5. **设置页新增"打开日志目录"按钮**：点击直接调 `open`（macOS）/ `xdg-open`（Linux）弹文件管理器，方便用户一键定位日志。

## 非目标

- **不做 systemd journal 双写**：Linux 用户可能习惯 `journalctl --user`，但双写会让逻辑复杂化，暂不做，文件落盘足够。
- **不做远程日志上报**：一切本地，不向外发任何东西。
- **不做日志脱敏**：识别文本本身不会写入日志（WARNING/ERROR 才记录），用户音频更不会。

## 偏好

- **选 `structlog`**：用户明确偏好，理由是结构化日志对后续排查 / 可观测性更友好。BACKLOG 原话"现在没到那个阶段"的判断被用户覆盖。
- stdlib `logging` 作为底层 backend（`structlog` 接 stdlib logging），不引入 `loguru`。
- 不加彩色输出（结构化日志的 key-value 在终端已经足够可读，彩色反而会让日志文件里混入 ANSI 码）—— 但可以给开发态一个 console renderer，生产（文件）走 JSON / logfmt。

## 约束

- **替换 `print()` 要一次做完**，不要留"一半 `print` 一半 `logger`"的中间状态。
- **plist 模板同步**：`StandardErrorPath` 路径在 plist 生成时写死，如果用户改了 `$HOME` 或自定义日志位置，需要想清楚 config 和 plist 的同步策略（最保守的做法：plist 里只放一个默认路径，用户自定义位置不会被 LaunchAgent 拾到，但应用自己的 logger 会按 config 走）。
