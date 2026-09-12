"""热键监听模块 - 使用 evdev 监听键盘事件，支持区分左右修饰键。"""

import contextlib
import fcntl
import os
import select
import threading
import time
from collections.abc import Callable

import evdev
from evdev import ecodes

from daobidao.i18n import t
from daobidao.logger import get_logger

logger = get_logger(__name__)

# 支持的热键映射
SUPPORTED_KEYS = {
    "KEY_RIGHTCTRL": ecodes.KEY_RIGHTCTRL,
    "KEY_LEFTCTRL": ecodes.KEY_LEFTCTRL,
    "KEY_RIGHTALT": ecodes.KEY_RIGHTALT,
    "KEY_LEFTALT": ecodes.KEY_LEFTALT,
    "KEY_RIGHTMETA": ecodes.KEY_RIGHTMETA,  # 右Win/Super键
    "KEY_LEFTMETA": ecodes.KEY_LEFTMETA,  # 左Win/Super键
    "KEY_CAPSLOCK": ecodes.KEY_CAPSLOCK,
    "KEY_F1": ecodes.KEY_F1,
    "KEY_F2": ecodes.KEY_F2,
    "KEY_F12": ecodes.KEY_F12,
}

# 组合键延迟（秒）：按下热键后等待此时间，期间无其他键按下才触发录音
COMBO_DELAY = 0.3

# 空闲时最长的等待窗口（秒）。任一设备有事件时 select 会立刻返回，这个值
# 只决定「select 不认的设备」最坏多久被扫到一次，见 _wait_for_events。
_IDLE_WAIT_S = 0.05

# 键盘全掉线后多久重扫一次设备列表（秒）。插回来不需要重启进程。
_REDISCOVER_INTERVAL_S = 1.0


def _set_nonblocking(devices: list[evdev.InputDevice]) -> None:
    """把所有设备的 fd 设为非阻塞。

    ``read_one()`` 在阻塞 fd 上会一直卡住（它内部就是一次 read(2)），
    必须显式设成 O_NONBLOCK 才能"没有事件就返回 None"。
    """
    for device in devices:
        try:
            fd = device.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except (OSError, AttributeError, ValueError):
            # 设备已拔掉,或者不是真 fd(测试里的 fake)
            continue


def _wait_for_events(devices: list[evdev.InputDevice], timeout: float) -> None:
    """等任一设备可读，最多等 ``timeout`` 秒。

    **返回值刻意丢弃**：``select()`` 在这里只当「能被打断的 sleep」用，
    不是「哪些设备有事件」的权威答案。部分 USB HID 键盘（典型是一个物理
    键盘暴露两个 evdev 节点的那种）的 fd 永远不会被 select 报可读，只处理
    select 返回的设备就会**静默丢事件**（issue #18：按热键毫无反应，换把
    键盘就好）。所以调用方醒来后会把**所有**设备都 drain 一遍（见
    ``_drain_devices``）—— select 只负责在有人敲键时立刻叫醒我们，
    从而不必忙等。

    fd 失效（拔键盘）时 select 抛 OSError，吞掉即可：裁剪交给 drain。
    """
    try:
        select.select(devices, [], [], timeout)
    except (OSError, ValueError):
        return


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
                    logger.debug(
                        "keyboard_found",
                        name=device.name,
                        path=device.path,
                        message=t(
                            "hotkey.found_keyboard",
                            name=device.name,
                            path=device.path,
                        ),
                    )
        except (PermissionError, OSError):
            continue
    return keyboards


class HotkeyListener:
    """监听键盘热键的按下和释放事件。

    使用 evdev 直接读取键盘设备，可以区分左右修饰键。
    需要 root 权限或将用户加入 input 组。

    对于修饰键（Ctrl/Alt/Meta），使用延迟触发机制避免与组合键冲突：
    按下热键后等待 COMBO_DELAY 秒，期间如果有其他键按下则视为组合键，
    不触发录音。
    """

    def __init__(
        self,
        hotkey: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ):
        key_code = SUPPORTED_KEYS.get(hotkey)
        if key_code is None:
            raise ValueError(
                f"Unsupported hotkey: {hotkey}, "
                f"supported: {list(SUPPORTED_KEYS.keys())}"
            )

        self.key_code = key_code
        self.hotkey_name = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self._running = False
        self._thread: threading.Thread | None = None

        # 热键状态
        self._pressed = False
        # 是否已激活录音（延迟确认后）
        self._activated = False
        # 是否被组合键取消
        self._cancelled = False
        # 延迟触发定时器
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

        # 判断是否为修饰键（需要延迟触发）
        self._is_modifier = key_code in {
            ecodes.KEY_RIGHTCTRL,
            ecodes.KEY_LEFTCTRL,
            ecodes.KEY_RIGHTALT,
            ecodes.KEY_LEFTALT,
            ecodes.KEY_RIGHTMETA,
            ecodes.KEY_LEFTMETA,
        }

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
        if self._timer:
            self._timer.cancel()
        if self._thread:
            self._thread.join(timeout=2)

    def _on_delayed_press(self) -> None:
        """延迟触发：确认不是组合键后激活录音。"""
        with self._lock:
            if not self._pressed or self._cancelled:
                return
            self._activated = True
        self.on_press()

    def _listen_loop(self) -> None:
        """监听循环。"""
        keyboards = find_keyboard_devices()
        if not keyboards:
            logger.error(
                "no_keyboard",
                message=t("hotkey.no_keyboard"),
                hint_run_as_root=t("hotkey.run_as_root"),
                hint_input_group=t("hotkey.add_input_group"),
            )
            return

        logger.info(
            "hotkey_listening",
            hotkey=self.hotkey_name,
            keyboards=len(keyboards),
            keyboard_names=[kb.name for kb in keyboards],
            message=t("hotkey.listening", hotkey=self.hotkey_name),
        )

        _set_nonblocking(keyboards)

        while self._running:
            if not keyboards:
                # 全掉线:周期性重扫,插回来不用重启进程(很便宜,一次
                # list_devices + 每台设备一次 capabilities)。
                time.sleep(_REDISCOVER_INTERVAL_S)
                keyboards = find_keyboard_devices()
                if keyboards:
                    _set_nonblocking(keyboards)
                    logger.info(
                        "hotkey_keyboards_reattached",
                        keyboards=len(keyboards),
                        keyboard_names=[kb.name for kb in keyboards],
                    )
                continue

            _wait_for_events(keyboards, _IDLE_WAIT_S)
            self._drain_devices(keyboards)

        # 清理
        for kb in keyboards:
            with contextlib.suppress(Exception):
                kb.close()

    def _drain_devices(self, keyboards: list[evdev.InputDevice]) -> None:
        """把所有设备里就绪的事件全部读出来。

        遍历的是**全部**设备,不是 ``select()`` 报可读的那几台 —— 有的键盘
        fd 永远不会被 select 报可读,只看 select 就会丢事件(issue #18),
        理由见 ``_wait_for_events``。
        """
        for device in list(keyboards):
            try:
                while True:
                    event = device.read_one()
                    if event is None:
                        break
                    if event.type == ecodes.EV_KEY:
                        self._handle_key_event(event)
            except BlockingIOError:
                # 非阻塞 fd 上「暂时没数据」的另一种表达:evdev 的 read()
                # 空闲时抛这个,read_one() 则返回 None。都不是掉线。
                continue
            except OSError:
                # 设备被拔掉 / 读取失败:就地摘掉。留着它下一轮 select 会
                # 直接抛 OSError,把整个监听线程带走 —— 热键从此静默失效。
                self._drop_device(keyboards, device)

    @staticmethod
    def _drop_device(
        keyboards: list[evdev.InputDevice], device: evdev.InputDevice
    ) -> None:
        """把失效设备从监听列表里摘掉并关闭。"""
        with contextlib.suppress(ValueError):
            keyboards.remove(device)
        with contextlib.suppress(Exception):
            device.close()
        logger.warning(
            "hotkey_device_lost",
            name=getattr(device, "name", "?"),
            path=getattr(device, "path", "?"),
            remaining=len(keyboards),
        )

    def _handle_key_event(self, event) -> None:
        """处理单个按键事件。"""
        if event.code == self.key_code:
            if event.value == 1 and not self._pressed:
                # 热键按下
                self._on_hotkey_press()
            elif event.value == 0 and self._pressed:
                # 热键释放
                self._on_hotkey_release()
            # value == 2 是按键重复，忽略
        elif event.value == 1 and self._pressed and not self._activated:
            # 热键按住期间有其他键按下 → 组合键，取消触发
            self._on_combo_detected()

    def _on_hotkey_press(self) -> None:
        """热键按下处理。"""
        with self._lock:
            self._pressed = True
            self._activated = False
            self._cancelled = False

        if self._is_modifier:
            # 修饰键：延迟触发，等待确认不是组合键
            self._timer = threading.Timer(COMBO_DELAY, self._on_delayed_press)
            self._timer.start()
        else:
            # 非修饰键（F1/F2等）：立即触发
            self._activated = True
            self.on_press()

    def _on_hotkey_release(self) -> None:
        """热键释放处理。"""
        with self._lock:
            self._pressed = False
            was_activated = self._activated
            self._activated = False

            # 取消未触发的定时器
            if self._timer:
                self._timer.cancel()
                self._timer = None

        if was_activated:
            self.on_release()

    def _on_combo_detected(self) -> None:
        """检测到组合键，取消触发。"""
        with self._lock:
            self._cancelled = True
            if self._timer:
                self._timer.cancel()
                self._timer = None
