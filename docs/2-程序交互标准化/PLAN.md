# 程序交互标准化 - 实现计划

## 技术方案

### 设置界面：浏览器 + 内置 HTTP 服务器
- 保留现有 pystray 托盘，添加"设置"菜单项
- 点击"设置"打开浏览器访问 `http://localhost:PORT`
- Python 内置 `http.server` 提供 HTML 页面和 REST API
- 无需 GTK3 等系统级 Python 包依赖

### 配置管理
- 新增 `ConfigManager` 类统一管理配置读写
- 开发模式使用项目目录 `config.yaml`，安装模式使用 `~/.config/whisper-input/config.yaml`
- 部分设置即时生效（语言、输入方式、提示音），部分需重启（快捷键、计算设备）

### DEB 打包
- 源码安装到 `/opt/whisper-input/`
- Python 依赖由 launcher 首次运行时 `uv sync` 安装（per-user venv）
- 系统依赖在 deb Depends 中声明

### 自启动
- 使用 XDG autostart（`~/.config/autostart/whisper-input.desktop`）
- 比 systemd 更适合需要 X11 DISPLAY 的桌面应用

## 实现步骤

1. `config_manager.py` - 配置管理器
2. `settings_server.py` - 设置页面 Web 服务 + HTML 页面
3. 重构 `main.py` - 集成设置服务、扩展托盘菜单
4. `assets/` - 应用图标、桌面文件
5. `debian/` + `build_deb.sh` - DEB 打包
6. 自启动支持集成在设置页面中
