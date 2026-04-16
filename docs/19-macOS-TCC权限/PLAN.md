# 实现计划：macOS TCC 权限

## 方案概述

构建一个 Objective-C 极简可执行文件，通过 `dlopen(libpython)` 在**同一进程内**加载 Python 解释器并运行 `whisper_input`。将该二进制打包为标准 macOS `.app` bundle，放在 `~/Applications/` 下。macOS TCC 系统将权限归属于该 `.app` bundle，而非 Python 解释器。

## 探索过程

1. **shell 脚本 + exec 到 Python**：验证失败。`exec()` 替换了进程镜像，TCC 看到的是 Python 二进制。
2. **C 二进制 dlopen libpython + ad-hoc 签名，放在 `/tmp/`**：进程正确运行，但 TCC 条目不显示在系统设置中。
3. **C 二进制 dlopen libpython + ad-hoc 签名，放在 `~/Applications/`**：**验证成功**。TCC 条目正常显示，权限对话框显示 app 名称。

关键发现：macOS 对 `/tmp/` 下的 app 区别对待；放在 `~/Applications/` 等正规路径下，ad-hoc 签名的 `.app` bundle 就能正常获得 TCC 条目，不需要 $99/年的 Developer ID。

## 技术架构

```
用户运行 whisper-input (Python console script)
  → 检测 macOS + .app bundle 已安装
  → open -a "~/Applications/Whisper Input.app"  (通过 LaunchServices 启动)
  → .app 内的 Objective-C 二进制启动
    → dlopen(libpython3.12.dylib)
    → Py_SetPythonHome(base_python_prefix)
    → Py_Initialize()
    → sys.path.insert(venv_site_packages)
    → from whisper_input.__main__ import main; main()
  → TCC 看到 "Whisper Input" ✓
```

## 文件变更

### 新增

| 文件 | 说明 |
|------|------|
| `launcher/macos/main.m` | Objective-C launcher 源码 |
| `launcher/macos/build.sh` | 编译脚本（clang + codesign） |
| `src/whisper_input/backends/app_bundle_macos.py` | .app bundle 管理模块 |

### 修改

| 文件 | 说明 |
|------|------|
| `src/whisper_input/__main__.py` | 加入 `--install-app` 参数 + macOS bundle 重定向逻辑 |
| `src/whisper_input/backends/autostart_macos.py` | LaunchAgent plist 优先使用 `open -a` |
| `src/whisper_input/backends/hotkey_macos.py` | bundle 模式下重启改用 `open -a` |
| `src/whisper_input/settings_server.py` | 同上 |

## 实现细节

### 1. Objective-C Launcher (`launcher/macos/main.m`)

- `[NSApplication sharedApplication]` + `NSApplicationActivationPolicyAccessory`（不显示 Dock 图标）
- `dispatch_async` 在后台线程运行 Python
- `dlopen(libpython3.12.dylib)` → `Py_SetPythonHome` → `Py_Initialize` → `PyRun_SimpleString`
- venv 路径从 `~/.config/whisper-input/venv-path` 读取
- base Python prefix 从 venv 的 `pyvenv.cfg` 解析

### 2. .app Bundle 结构

```
~/Applications/Whisper Input.app/
  Contents/
    Info.plist          ← CFBundleName, CFBundleIdentifier, LSUIElement=true
    MacOS/
      whisper-input     ← 编译好的 Objective-C 二进制
    Resources/
      AppIcon.icns      ← 从 whisper-input.png 转换
```

### 3. `--install-app` 命令

`whisper-input --install-app` 执行以下操作：
1. 编译 launcher（需要 clang，macOS 自带或通过 Xcode CLI Tools）
2. 生成 .app bundle 到 `~/Applications/Whisper Input.app/`
3. Ad-hoc 签名
4. 写入 venv 路径到 `~/.config/whisper-input/venv-path`
5. 提示用户后续操作

### 4. 自动重定向

`main()` 开头检测：
- `sys.platform == "darwin"`
- `_WHISPER_INPUT_BUNDLE` 环境变量不存在（避免循环）
- `.app` bundle 已安装
→ 调用 `open -a` 启动 .app，当前进程退出

### 5. LaunchAgent 适配

`autostart_macos.py` 的 `_program_arguments()` 优先返回：
```python
["/usr/bin/open", "-g", "-a", app_bundle_path]
```

### 6. 重启逻辑适配

`hotkey_macos.py:check_macos_permissions()` 和 `settings_server.py:_handle_restart()`：
- 检测 `_WHISPER_INPUT_BUNDLE` 环境变量
- 如在 bundle 模式：`subprocess.Popen(["open", "-a", ...])` + `sys.exit()`
- 否则保持原 `os.execv` 行为

## 验证方法

1. `whisper-input --install-app` → 检查 `~/Applications/Whisper Input.app/` 结构
2. `whisper-input` → 自动通过 `open -a` 重启
3. 系统设置 → 辅助功能/输入监控 → 显示 "Whisper Input"
4. 授权后语音输入正常工作
5. `uv run pytest` 全部通过
6. `uv run ruff check .` 无错误
