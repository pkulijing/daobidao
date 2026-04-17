# 开发总结：macOS TCC 权限问题

## 开发项背景

macOS TCC（Transparency, Consent, and Control）系统将权限归属于实际调用受保护 API 的进程二进制。通过 `uv tool install` 安装后，运行 `whisper-input` 的实际进程是 Python 解释器（`python3.12`），导致：

- 权限对话框显示 "python3.12 wants to access..." — 用户困惑
- 系统设置里显示一个深埋在 `~/.local/share/uv/` 下的 Python 路径 — 无品牌感
- `uv tool upgrade` 换 Python 版本后权限可能失效

## 实现方案

### 关键设计

**核心发现 1**：macOS TCC 检查的是**实际运行受保护 API 的进程二进制**，不是启动上下文。因此：

- `exec()` 到 Python → 不行（进程镜像被替换，TCC 看到 Python）
- `fork+exec` 子进程 → 不行（子进程是 Python）
- **`dlopen(libpython)` 在自身进程内运行 Python → 可行**（进程始终是我们的二进制）

**核心发现 2**：`.app` bundle 必须放在正规路径（如 `~/Applications/`）。放在 `/tmp/` 下，TCC 条目不会显示在系统设置 UI 中。

**核心发现 3**：ad-hoc 签名（`codesign -s -`）对 TCC 足够，不需要 $99/年的 Apple Developer ID。

**核心发现 4：只需 Accessibility，不需 Input Monitoring**。pynput 使用 `kCGSessionEventTap` + `kCGEventTapOptionListenOnly`（session 级事件监听），Accessibility 权限已涵盖。Input Monitoring（`kTCCServiceListenEvent`）是给 `kCGHIDEventTap`（HID 底层事件拦截）用的，我们没用那一层。这个发现是调试中实证得出的 — 只授权 Accessibility、不授权 Input Monitoring，热键监听和语音输入完全正常工作。

**核心发现 5：权限检查时必须保持 runloop 活跃**。launcher 初始化了 `[NSApplication sharedApplication]` 但在 pystray 启动前不会调用 `[NSApp run]`。如果在 `check_macos_permissions()` 中用 `time.sleep()` 阻塞等待用户授权，会导致两个问题：
- LaunchServices 的启动事件无人处理 → `open -a` 超时报 -1712
- TCC 系统弹窗无法显示（需要主 runloop 派发）

解决方案：用 `CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.5, False)` 代替 `time.sleep(0.5)`，每次轮询时抽空跑一下主 runloop。

### 开发内容概括

1. **Objective-C 原生 launcher**（`launcher/macos/main.m`）
   - 初始化 NSApplication（让 macOS 认为是正常 app，避免"没有响应"）
   - 设置 `PYTHONIOENCODING=utf-8` 和 `LC_ALL=en_US.UTF-8`（`.app` 启动时没有终端 locale，否则中文 `print()` 会 `UnicodeEncodeError`）
   - 从 `~/.config/whisper-input/venv-path` 读取 venv 位置
   - 从 `pyvenv.cfg` 解析 base Python prefix
   - `dlopen(libpython3.12.dylib)` → `Py_SetPythonHome` → `Py_Initialize`
   - 用 `site.addsitedir()` 加载 venv site-packages（支持 editable install 的 `.pth` 文件）
   - **直接在主线程运行 Python**（不用 `dispatch_async`），让 pystray 的 `icon.run()` 调用 `[NSApp run]` 启动事件循环。AppKit 要求 UI 操作必须在主线程
   - 调用 `from whisper_input.__main__ import main; main()`
   - 所有依赖均为 macOS 系统自带框架（Cocoa、libSystem），无第三方依赖

2. **预编译 + CI 分发**
   - `launcher/macos/build.sh`：构建 universal binary（arm64 + x86_64）+ 生成 AppIcon.icns
   - 产物存放于 `src/whisper_input/assets/macos/`，通过 hatch build hook `force_include` 打入 wheel
   - build hook 同时检查 `src/whisper_input/assets/macos/`（源码树）和 `whisper_input/assets/macos/`（sdist 解包路径），因为 `uv build` 先生成 sdist 再从 sdist 构建 wheel
   - `release.yml` 改为 `macos-latest` runner，发版时自动构建
   - 用户端**不需要 clang / Xcode CLI Tools**，直接使用预编译二进制

3. **`.app` bundle 管理模块**（`src/whisper_input/backends/app_bundle_macos.py`）
   - `install_app_bundle()`：复制预编译 binary + Info.plist + icns → ad-hoc 签名
   - `is_app_bundle_outdated()`：比较 Info.plist 中的版本号与当前包版本，升级时自动重装
   - `launch_via_bundle()`：通过 `open -a` 启动 `.app`，当前进程退出
   - `restart_via_bundle()`：`os.execv` 到 launcher binary（不用 `open -a`，避免 LaunchServices 缓存过时导致 -600 错误）
   - `uninstall_cleanup()`：交互式清理 .app / LaunchAgent / TCC / 配置 / 模型缓存

4. **首次运行自动安装 + 版本升级检测**（`__main__.py`）
   - macOS 首次运行时自动调用 `install_app_bundle()` 生成 `.app`
   - 检测 `.app` 版本与包版本不一致时自动重装（适配 `uv tool upgrade`）
   - 后续每次运行自动通过 `open -a` 重定向到 `.app`
   - `update_venv_path()` 在每次重定向前更新 venv 路径

5. **`--init` 一次性初始化**（`__main__.py`）
   - macOS：安装 `.app` bundle
   - 全平台：下载 STT 模型（约 231 MB）
   - 推荐安装后立即运行，避免首次启动时长时间等待

6. **`--uninstall` 卸载清理**（`app_bundle_macos.py`）
   - 自动清理：LaunchAgent / TCC 授权 / .app bundle / venv-path
   - 交互确认（`[y/N]`）：配置文件 / 模型缓存
   - 兼容 modelscope 新旧版缓存路径（`hub/models/iic/` 和 `hub/iic/`）

7. **权限检查简化**（`hotkey_macos.py`）
   - 只检查 Accessibility（不需要 Input Monitoring）
   - `AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})` 触发系统原生弹窗
   - 轮询用 `CFRunLoopRunInMode` 保持 runloop 活跃
   - 授权到位后 `os.execv` 重启（event tap 需在新进程中创建）

8. **LaunchAgent 适配**（`autostart_macos.py`）
   - `_program_arguments()` 直接指向 `.app` 内的可执行文件（而非 `open -g -a`，否则登录项里显示 "open" 而非 app 名称）

9. **重启逻辑适配**（`hotkey_macos.py`、`settings_server.py`）
   - bundle 模式下用 `os.execv` 重启到 launcher binary，保持 TCC 上下文

### 额外产物

- `launcher/macos/build.sh`：完整构建脚本（launcher + icns）
- `scripts/hatch_build.py`：扩展了 macOS assets 的 `force_include`（兼容 sdist 路径）
- `scripts/dev_reinstall.sh`：本地构建 + `uv tool install` 闭环测试脚本，支持 `--wipe-all` 全清
- 测试修复：autostart 测试适配新的 bundle 优先级逻辑
- README 更新：推荐 `--compile-bytecode`、`--init` 流程，更新权限说明

## 局限性

1. **Python 版本硬编码**：launcher 中 `libpython3.12.dylib` 和 `python3.12/site-packages` 路径写死了 3.12。如果项目支持的 Python 版本变化，launcher 源码需要更新
2. **直接从 `.app` 启动时 venv 路径可能过时**：如果用户直接双击 `.app`（未经过 Python 入口的 `update_venv_path()`），`uv tool upgrade` 后 venv 路径可能不匹配。正常的终端启动和 LaunchAgent 启动不受影响
3. **cdhash 级别的 TCC 记忆**：`tccutil reset <service> <bundle_id>` 按 bundle ID 清除 TCC 条目，但 macOS 内部会按 cdhash 记忆授权。launcher binary 不变时，重装 `.app` 不会要求重新授权（对用户是好事，对测试是阻碍 — 需要 `sudo tccutil reset All` 才能彻底清除）

## 后续 TODO

- 支持动态检测 Python 版本（而非硬编码 3.12）
