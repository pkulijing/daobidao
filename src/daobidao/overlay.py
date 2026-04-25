"""录音浮窗调度器 - 按平台导入对应实现。"""

from daobidao.backends import IS_MACOS

if IS_MACOS:
    from daobidao.backends.overlay_macos import RecordingOverlay
else:
    from daobidao.backends.overlay_linux import RecordingOverlay

__all__ = ["RecordingOverlay"]
