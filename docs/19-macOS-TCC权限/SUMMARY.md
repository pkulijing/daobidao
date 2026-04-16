# 开发总结：macOS TCC 权限问题

## 开发项背景

macOS TCC（Transparency, Consent, and Control）系统将权限归属于实际调用受保护 API 的进程二进制。通过 `uv tool install` 安装后，运行 `whisper-input` 的实际进程是 Python 解释器（`python3.12`），导致：

- 权限对话框显示 "python3.12 wants to access..." — 用户困惑
- 系统设置里显示一个深埋在 `~/.local/share/uv/` 下的 Python 路径 — 无品牌感
- `uv tool upgrade` 换 Python 版本后权限可能失效

## 实现方案

### 关键设计

**核心发现**：macOS TCC 检查的是**实际运行受保护 API 的进程二进制**，不是启动上下文。因此：

- `exec()` 到 Python → 不行（进程镜像被替换，TCC 看到 Python）
- `fork+exec` 子进程 → 不行（子进程是 Python）
- **`dlopen(libpython)` 在自身进程内运行 Python → 可行**（进程始终是我们的二进制）

**第二个关键发现**：`.app` bundle 必须放在正规路径（如 `~/Applications/`）。放在 `/tmp/` 下，TCC 条目不会显示在系统设置 UI 中。

**第三个关键发现**：ad-hoc 签名（`codesign -s -`）对 TCC 足够，不需要 $99/年的 Apple Developer ID。

### 开发内容概括

1. **Objective-C 原生 launcher**（`launcher/macos/main.m`）
   - 初始化 NSApplication（让 macOS 认为是正常 app，避免"没有响应"）
   - 从 `~/.config/whisper-input/venv-path` 读取 venv 位置
   - 从 `pyvenv.cfg` 解析 base Python prefix
   - `dlopen(libpython3.12.dylib)` → `Py_SetPythonHome` → `Py_Initialize`
   - 用 `site.addsitedir()` 加载 venv site-packages（支持 editable install 的 `.pth` 文件）
   - 调用 `from whisper_input.__main__ import main; main()`
   - 所有依赖均为 macOS 系统自带框架（Cocoa、libSystem），无第三方依赖

2. **预编译 + CI 分发**
   - `launcher/macos/build.sh`：构建 universal binary（arm64 + x86_64）+ 生成 AppIcon.icns
   - 产物存放于 `src/whisper_input/assets/macos/`，通过 hatch build hook `force_include` 打入 wheel
   - `release.yml` 改为 `macos-latest` runner，发版时自动构建
   - 用户端**不需要 clang / Xcode CLI Tools**，直接使用预编译二进制

3. **`.app` bundle 管理模块**（`src/whisper_input/backends/app_bundle_macos.py`）
   - `install_app_bundle()`：复制预编译 binary + Info.plist + icns → ad-hoc 签名
   - `launch_via_bundle()`：通过 `open -a` 启动 `.app`
   - `restart_via_bundle()`：在 bundle 模式下安全重启

4. **首次运行自动安装**（`__main__.py`）
   - macOS 首次运行时自动调用 `install_app_bundle()` 生成 `.app`
   - 后续每次运行自动通过 `open -a` 重定向到 `.app`
   - `update_venv_path()` 在每次重定向前更新 venv 路径（适应 `uv tool upgrade`）
   - 保留 `--install-app` 参数供手动重装

5. **LaunchAgent 适配**（`autostart_macos.py`）
   - `_program_arguments()` 优先返回 `open -g -a "Whisper Input.app"`

6. **重启逻辑适配**（`hotkey_macos.py`、`settings_server.py`）
   - bundle 模式下用 `open -a` 重启，保持 TCC 上下文

### 额外产物

- `launcher/macos/build.sh`：完整构建脚本（launcher + icns）
- `scripts/hatch_build.py`：扩展了 macOS assets 的 `force_include`
- 测试修复：autostart 测试适配新的 bundle 优先级逻辑

## 局限性

1. **Python 版本硬编码**：launcher 中 `libpython3.12.dylib` 和 `python3.12/site-packages` 路径写死了 3.12。如果项目支持的 Python 版本变化，launcher 源码需要更新
2. **直接从 `.app` 启动时 venv 路径可能过时**：如果用户直接双击 `.app`（未经过 Python 入口的 `update_venv_path()`），`uv tool upgrade` 后 venv 路径可能不匹配。正常的终端启动和 LaunchAgent 启动不受影响

## 后续 TODO

- 支持动态检测 Python 版本（而非硬编码 3.12）
