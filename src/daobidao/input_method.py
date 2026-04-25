"""文字输入模块 - 运行时按平台选择后端实现。"""

from daobidao.backends import IS_MACOS

if IS_MACOS:
    from daobidao.backends.input_macos import type_text
else:
    from daobidao.backends.input_linux import type_text

__all__ = ["type_text"]
