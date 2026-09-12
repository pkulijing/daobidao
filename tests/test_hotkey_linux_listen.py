"""``hotkey_linux`` 的监听循环(issue #18)。

现象:部分 USB 键盘(典型是一个物理键盘暴露两个 evdev 节点,如 HUIZI
VSG KEY2.4)按热键毫无反应,换一把键盘就正常。

根因:老实现用 ``select.select(keyboards, [], [], 0.5)`` 的**返回值**决定读哪
台设备。这些键盘的 fd 永远不会被 select 报可读,于是它们的事件一路被静默丢掉
—— select 返回空,循环就空转一圈,事件还躺在 fd 里。

修法:select 降级成"能被打断的 sleep"(有键按下时立刻返回,不必忙等),
醒来后把**所有**设备都 drain 一遍。本文件就盯住这个语义:

- ``test_events_arrive_even_when_select_never_reports_readable`` 是那个回归
  测试 —— 老实现必红
- 另外覆盖拔键盘后不能把监听线程带走(老实现里 select 会直接抛 OSError)

测试只调 ``_listen_loop``(经 ``start()``),用假设备替换真实 fd,不碰
``/dev/input``;不用 ``.start()`` 的话 ``_running`` 逻辑就测不到了。
"""

from __future__ import annotations

import fcntl
import os
import time

import pytest

from daobidao.backends.hotkey_linux import ecodes

EV_KEY = ecodes.EV_KEY
KEY_F12 = ecodes.KEY_F12


def _load():
    """拿真正的 hotkey_linux 模块(conftest 注入的 fake evdev 已就位)。"""
    from daobidao.backends import hotkey_linux

    return hotkey_linux


@pytest.fixture
def mod():
    return _load()


class _BlindSelect:
    """select() 永远回「没有设备可读」—— 复现 issue #18 的键盘。

    同时把等待缩到 1 ms,避免测试真的按 50 ms 一轮跑。
    """

    def __init__(self, delay: float = 0.001):
        self.delay = delay
        self.calls = 0

    def select(self, rlist, wlist, xlist, timeout):
        self.calls += 1
        time.sleep(self.delay)
        return ([], [], [])


class _FakeEvent:
    def __init__(self, type_: int, code: int, value: int):
        self.type = type_
        self.code = code
        self.value = value


class _FakeKeyboard:
    """只实现监听循环用到的那几个方法。"""

    def __init__(self, name: str, events=(), always_fail: bool = False):
        self.name = name
        self.path = f"/dev/input/fake-{name}"
        self.closed = False
        self.reads = 0
        self._events = list(events)
        self._always_fail = always_fail

    def fileno(self):
        # 不是真 fd → _set_nonblocking 应当跳过而不是炸
        raise AttributeError("fake keyboard has no fileno")

    def read_one(self):
        self.reads += 1
        if self._always_fail:
            raise OSError(19, "No such device")
        if self._events:
            return self._events.pop(0)
        return None

    def close(self):
        self.closed = True


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _make_listener(mod, devices, monkeypatch, select_impl):
    monkeypatch.setattr(mod, "find_keyboard_devices", lambda: list(devices))
    monkeypatch.setattr(mod, "select", select_impl)
    presses: list[None] = []
    releases: list[None] = []
    listener = mod.HotkeyListener(
        hotkey="KEY_F12",  # 非修饰键 → 按下立即触发,不用等 combo 延迟
        on_press=lambda: presses.append(None),
        on_release=lambda: releases.append(None),
    )
    return listener, presses, releases


def test_events_arrive_even_when_select_never_reports_readable(
    mod, monkeypatch
):
    """issue #18 回归测试:select 从不报可读的键盘,事件也必须送达。"""
    kb = _FakeKeyboard(
        "HUIZI VSG KEY2.4",
        [
            _FakeEvent(EV_KEY, KEY_F12, 1),  # 按下
            _FakeEvent(EV_KEY, KEY_F12, 0),  # 松开
        ],
    )
    blind = _BlindSelect()
    listener, presses, releases = _make_listener(mod, [kb], monkeypatch, blind)

    listener.start()
    try:
        assert _wait_until(lambda: bool(releases)), (
            "select 报不可读的设备,事件被丢了 —— 老实现就是这么坏的"
        )
    finally:
        listener.stop()

    assert presses == [None]
    assert releases == [None]
    assert blind.calls > 0  # 确认真的走了 select 那条路


def test_dead_device_is_dropped_and_loop_survives(mod, monkeypatch):
    """拔掉的设备会被摘掉,同轮其它键盘照常收到事件,线程不能死。"""
    dead = _FakeKeyboard("unplugged", always_fail=True)
    alive = _FakeKeyboard(
        "still-here",
        [_FakeEvent(EV_KEY, KEY_F12, 1), _FakeEvent(EV_KEY, KEY_F12, 0)],
    )
    listener, presses, releases = _make_listener(
        mod, [dead, alive], monkeypatch, _BlindSelect()
    )

    listener.start()
    try:
        assert _wait_until(lambda: bool(releases))
        assert _wait_until(lambda: dead.closed)
        # 循环还活着:同轮的活键盘没被误伤(收尾的 close 还没轮到它)
        assert not alive.closed
    finally:
        listener.stop()

    assert presses == [None]
    assert dead.closed


def test_rediscovers_keyboards_after_all_devices_are_lost(mod, monkeypatch):
    """全部键盘掉线后周期性重扫:插回来不用重启进程。"""
    monkeypatch.setattr(mod, "_REDISCOVER_INTERVAL_S", 0.01)
    dead = _FakeKeyboard("unplugged", always_fail=True)
    revived = _FakeKeyboard(
        "plugged-back",
        [_FakeEvent(EV_KEY, KEY_F12, 1), _FakeEvent(EV_KEY, KEY_F12, 0)],
    )
    scans: list[int] = []

    def find():
        scans.append(len(scans) + 1)
        return [dead] if len(scans) == 1 else [revived]

    listener, presses, releases = _make_listener(
        mod, [dead], monkeypatch, _BlindSelect()
    )
    monkeypatch.setattr(mod, "find_keyboard_devices", find)

    listener.start()
    try:
        assert _wait_until(lambda: bool(releases), timeout=3.0)
    finally:
        listener.stop()

    assert len(scans) >= 2  # 掉线后确实又扫了一次
    assert presses == [None]


def test_none_key_fd_is_ignored_not_fatal():
    """fd 不是真 fd(或已被拔)的设备,设非阻塞时跳过即可,不能抛。"""

    class _NoFileno:
        def fileno(self):
            raise OSError(9, "Bad file descriptor")

    class _Plain:
        pass

    _load()._set_nonblocking([_NoFileno(), _Plain()])


def test_set_nonblocking_sets_o_nonblock(mod):
    """真 fd 上必须真的设上 O_NONBLOCK —— read_one() 靠它才能不阻塞。"""
    read_fd, write_fd = os.pipe()

    class _PipeDevice:
        def fileno(self):
            return read_fd

    try:
        mod._set_nonblocking([_PipeDevice()])
        flags = fcntl.fcntl(read_fd, fcntl.F_GETFL)
        assert flags & os.O_NONBLOCK
    finally:
        os.close(read_fd)
        os.close(write_fd)
