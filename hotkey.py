"""热键监听模块 - 使用 evdev 监听键盘事件，支持区分左右修饰键。"""

import evdev
from evdev import ecodes, InputEvent
import select
import threading
from typing import Callable


# 支持的热键映射
SUPPORTED_KEYS = {
    "KEY_RIGHTCTRL": ecodes.KEY_RIGHTCTRL,
    "KEY_LEFTCTRL": ecodes.KEY_LEFTCTRL,
    "KEY_RIGHTALT": ecodes.KEY_RIGHTALT,
    "KEY_LEFTALT": ecodes.KEY_LEFTALT,
    "KEY_RIGHTMETA": ecodes.KEY_RIGHTMETA,   # 右Win/Super键
    "KEY_LEFTMETA": ecodes.KEY_LEFTMETA,     # 左Win/Super键
    "KEY_CAPSLOCK": ecodes.KEY_CAPSLOCK,
    "KEY_F1": ecodes.KEY_F1,
    "KEY_F2": ecodes.KEY_F2,
    "KEY_F12": ecodes.KEY_F12,
}


def find_keyboard_devices() -> list[evdev.InputDevice]:
    """查找所有键盘设备。"""
    keyboards = []
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
            caps = device.capabilities(verbose=False)
            # EV_KEY 事件类型 = 1
            if ecodes.EV_KEY in caps:
                key_caps = caps[ecodes.EV_KEY]
                # 检查是否有常见的键盘按键（字母键）
                if ecodes.KEY_A in key_caps and ecodes.KEY_Z in key_caps:
                    keyboards.append(device)
                    print(f"[hotkey] 发现键盘: {device.name} ({device.path})")
        except (PermissionError, OSError):
            continue
    return keyboards


class HotkeyListener:
    """监听键盘热键的按下和释放事件。

    使用 evdev 直接读取键盘设备，可以区分左右修饰键。
    需要 root 权限或将用户加入 input 组。
    """

    def __init__(
        self,
        hotkey: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ):
        key_code = SUPPORTED_KEYS.get(hotkey)
        if key_code is None:
            raise ValueError(f"不支持的热键: {hotkey}，支持的热键: {list(SUPPORTED_KEYS.keys())}")

        self.key_code = key_code
        self.hotkey_name = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self._running = False
        self._thread: threading.Thread | None = None
        self._pressed = False

    def start(self) -> None:
        """开始监听热键（在后台线程中运行）。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止监听。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _listen_loop(self) -> None:
        """监听循环。"""
        keyboards = find_keyboard_devices()
        if not keyboards:
            print("[hotkey] 错误: 未找到键盘设备。请确保:")
            print("  1. 以 root 运行，或")
            print("  2. 将用户加入 input 组: sudo usermod -aG input $USER")
            return

        print(f"[hotkey] 正在监听热键: {self.hotkey_name}")

        while self._running:
            # 使用 select 监听多个键盘设备
            r, _, _ = select.select(keyboards, [], [], 0.5)
            for device in r:
                try:
                    for event in device.read():
                        if event.type == ecodes.EV_KEY and event.code == self.key_code:
                            if event.value == 1 and not self._pressed:
                                # 按下
                                self._pressed = True
                                self.on_press()
                            elif event.value == 0 and self._pressed:
                                # 释放
                                self._pressed = False
                                self.on_release()
                            # value == 2 是按键重复，忽略
                except (OSError, BlockingIOError):
                    continue

        # 清理
        for kb in keyboards:
            try:
                kb.close()
            except Exception:
                pass
