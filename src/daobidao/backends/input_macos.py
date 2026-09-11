"""文字输入模块 (macOS) - 通过 pbcopy/pbpaste + pynput 输入到当前焦点窗口。

需要在「系统设置 > 隐私与安全性 > 辅助功能」中授权终端或应用。
"""

import contextlib
import subprocess
import time

from pynput.keyboard import Controller, Key

_keyboard = Controller()


def type_text(text: str) -> None:
    """将文字输入到当前焦点窗口（剪贴板 + Cmd+V 粘贴）。"""
    if not text:
        return
    _type_via_clipboard(text)


def _type_via_clipboard(text: str) -> None:
    """通过剪贴板粘贴文字（支持中文）。

    1. 保存当前剪贴板内容
    2. 将识别文字写入剪贴板
    3. 模拟 Cmd+V 粘贴
    4. 恢复原剪贴板内容
    """
    # 保存原剪贴板
    try:
        original = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            timeout=2,
            check=False,  # 剪贴板为空时当作「没有原内容」
        ).stdout
    except Exception:
        original = None

    try:
        # 写入新内容
        subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            timeout=2,
            check=True,
        )

        # 短暂等待剪贴板同步
        time.sleep(0.05)

        # 模拟 Cmd+V 粘贴
        try:
            _keyboard.press(Key.cmd)
            _keyboard.press("v")
        finally:
            # 释放必须无条件跑完:Cmd 按下后若中途抛出,修饰键会一直保持
            # 「按下」,此后用户敲的每个键都带 Cmd(w 关窗口、q 退应用),
            # 而异常已被调用方吞掉、界面上毫无提示。
            with contextlib.suppress(Exception):
                _keyboard.release("v")
            with contextlib.suppress(Exception):
                _keyboard.release(Key.cmd)
    finally:
        # 恢复原剪贴板。放 finally 里:pbcopy 写入之后的任何一步抛出(送键
        # 失败等),剪贴板都已经被识别结果顶掉,而原内容只存在 original 这个
        # 局部变量里 —— 不还原就随栈帧一起永久丢了。
        if original is not None:
            time.sleep(0.1)
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["pbcopy"],
                    input=original,
                    timeout=2,
                    check=False,  # 恢复失败不影响本次粘贴
                )
