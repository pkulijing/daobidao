"""系统托盘模块 - 运行时按平台选择后端实现。"""

from daobidao.backends import IS_MACOS

if IS_MACOS:
    from daobidao.backends.tray_macos import run_tray
else:
    from daobidao.backends.tray_linux import run_tray

__all__ = ["run_tray"]
