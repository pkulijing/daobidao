# macOS TCC 权限问题的优雅解决

## 问题

PyPI 路线下（`uv tool install` / `pipx install`），`whisper-input` 的实际进程是 Python 解释器二进制。macOS TCC（Transparency, Consent, and Control）系统将权限归属于实际二进制：

1. 权限对话框显示 "python3.12 wants to access..."，用户困惑
2. 系统设置里显示的是一个深埋在 `~/.local/share/uv/` 下的 Python 路径，没有品牌感
3. `uv tool upgrade` 换 Python 小版本后，权限可能失效

## 目标

用户装完后首次运行时：
- 系统设置中权限条目清楚标识是 "Whisper Input" 或类似名称
- 权限在 `uv tool upgrade` 后持续有效

## 人类发起的方向

用一个编译好的可执行文件替代 Python 作为进程入口，让 macOS TCC 认为运行的是我们自己的程序。
