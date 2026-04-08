# 程序交互标准化 - 开发总结

## 开发项背景

whisper-input 此前是开发者工具形态：命令行启动、手动编辑 YAML 配置、setup.sh 安装。对普通用户门槛较高，需要标准化为 Ubuntu 桌面应用的交互方式——系统托盘可视化、图形化设置、DEB 包安装、桌面菜单启动。

## 实现方案

### 关键设计

1. **浏览器设置页面替代 GTK**：最初计划用 GTK3 做设置界面，但 PyGObject 是系统级包，和 uv venv 配合复杂。改为 Python 内置 `http.server` 提供 Web 页面，托盘点击"设置"后打开浏览器，零额外依赖。
2. **AppIndicator3 托盘**：pystray 的 `_xorg` 后端在 GNOME 上菜单不响应，需要 AppIndicator3 后端。通过 `uv add PyGObject` 编译安装 + 设置 `GI_TYPELIB_PATH` 解决 typelib 路径不一致问题。`run_detached()` 在 appindicator 后端下不显示图标，改为 `threading.Thread(target=icon.run)` 解决。
3. **修饰键组合键冲突**：Ctrl 做热键会和 Ctrl+S/Z 等冲突。实现延迟触发机制——按下修饰键后等 300ms，期间有其他键按下则视为组合键取消触发。
4. **设置即时生效**：修改设置后自动保存并即时生效（提示音、输入方式、语言），无需手动点保存。快捷键和计算设备需重启。
5. **Per-user venv**：DEB 安装到 `/opt/whisper-input/`，Python 依赖安装到 `~/.local/share/whisper-input/.venv`，避免 root venv 权限问题。

### 开发内容概括

- **config_manager.py**：统一配置管理器，支持开发模式（项目目录）和安装模式（XDG 路径），提供 get/set 接口和默认值合并
- **settings_server.py**：内置 HTTP 服务器 + Ubuntu 风格 HTML 设置页面 + REST API（配置读写、自启动管理、退出）
- **main.py 重构**：集成设置服务器、扩展托盘菜单（设置/退出）、配置热更新回调、AppIndicator3 兼容处理
- **hotkey.py 重构**：修饰键延迟触发机制，区分单独按住和组合键操作
- **input_method.py**：添加 `--clearmodifiers` 防止热键释放干扰粘贴
- **debian/ 打包文件**：control（系统依赖声明含 PyGObject 编译依赖）、postinst（input 组 + uv 安装）、launcher 脚本（per-user venv + GI_TYPELIB_PATH）
- **assets/**：应用图标（PIL 生成 256x256 PNG）、.desktop 桌面入口文件

### 额外产物

- `assets/generate_icon.py` - 图标生成脚本，可重新生成不同尺寸
- `build_deb.sh` - DEB 包一键构建脚本
- `.claude/skills/start/` 和 `.claude/skills/finish/` - 开发流程 skill

## 局限性

- **Win/Super 键不可用**：GNOME 拦截 Super 键，evdev 收不到事件，已从设置界面移除
- **Alt 键粘贴干扰**：Alt 作为热键时释放会干扰 xdotool 的 Ctrl+V 模拟，`--clearmodifiers` 不完全解决，已从设置界面移除
- **VS Code 扩展面板兼容性**：xdotool 模拟按键对 VS Code 扩展 webview 面板可能不生效，普通编辑器和浏览器无此问题
- **首次启动耗时**：DEB 安装后首次启动需 `uv sync` 下载 PyTorch 等大量依赖（约 2GB）

## 后续 TODO

- Wayland 支持（当前仅 X11，需替换 xdotool/xclip 为 wtype/wl-clipboard）
- 设置页面增加模型选择和模型缓存路径配置
- 探索更好的文本输入方式（如 ydotool 或 DBus 输入法接口）解决 VS Code 兼容问题
- 考虑将 PyGObject 编译依赖从 Depends 移到 Build-Depends，预编译 wheel 打包进 deb 减少安装复杂度
