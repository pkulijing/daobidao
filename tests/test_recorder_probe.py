"""测试 AudioRecorder.probe() —— 32 轮新增的录音前麦克风可用性校验。

两条路径分别测:
- Linux 路径: 走 ``_check_pactl_input_available``,pactl 是唯一权威。
  覆盖各种 pactl 输出格式 / pactl 不存在 / 调用失败。
- 非 Linux 路径(macOS / 其他): 走 ``sd.query_devices``,monkeypatch
  ``sys.platform`` 让 probe 进入这条分支。
"""

from __future__ import annotations

import threading
import time
import types

import pytest


class _FakePortAudioError(Exception):
    pass


def _patch_sd(monkeypatch, query_fn):
    """把 recorder.sd.query_devices 替换成 query_fn。"""
    from daobidao import recorder as recorder_mod

    fake = types.ModuleType("sounddevice")
    fake.query_devices = query_fn
    fake.PortAudioError = _FakePortAudioError
    monkeypatch.setattr(recorder_mod, "sd", fake)


def _force_non_linux(monkeypatch):
    """让 probe 跳过 Linux/pactl 分支,走 query_devices 兜底路径。"""
    monkeypatch.setattr("daobidao.recorder.sys.platform", "darwin")


def _force_linux(monkeypatch):
    """让 probe 走 Linux/pactl 分支(在非 Linux 开发机上跑测时也强制走这条)。"""
    monkeypatch.setattr("daobidao.recorder.sys.platform", "linux")


def _patch_pactl(monkeypatch, fn):
    """替换 _check_pactl_input_available。fn 可以正常返回或抛 PactlUnavailableError。"""
    monkeypatch.setattr("daobidao.recorder._check_pactl_input_available", fn)


# ====================================================================
# Linux / pactl 路径
# ====================================================================


def test_probe_linux_succeeds_when_pactl_returns_true(monkeypatch):
    _force_linux(monkeypatch)
    _patch_pactl(monkeypatch, lambda: True)

    from daobidao.recorder import AudioRecorder

    AudioRecorder().probe()  # 不抛即通过


def test_probe_linux_raises_when_pactl_returns_false(monkeypatch):
    _force_linux(monkeypatch)
    _patch_pactl(monkeypatch, lambda: False)

    from daobidao.recorder import AudioRecorder, MicUnavailableError

    with pytest.raises(MicUnavailableError) as excinfo:
        AudioRecorder().probe()
    assert excinfo.value.reason == "probe_failed"
    assert "no available input port" in (excinfo.value.detail or "")


def test_probe_linux_raises_when_pactl_unavailable(monkeypatch):
    _force_linux(monkeypatch)

    from daobidao.recorder import (
        AudioRecorder,
        MicUnavailableError,
        PactlUnavailableError,
    )

    def _boom():
        raise PactlUnavailableError("pactl command not found")

    _patch_pactl(monkeypatch, _boom)

    with pytest.raises(MicUnavailableError) as excinfo:
        AudioRecorder().probe()
    assert excinfo.value.reason == "probe_failed"
    assert "pactl unavailable" in (excinfo.value.detail or "")
    assert "pulseaudio-utils" in (excinfo.value.detail or "")


def test_probe_linux_does_not_call_query_devices_on_pactl_success(
    monkeypatch,
):
    """pactl=True 时不应再调 query_devices(避免在 PipeWire 上的虚假成功)。"""
    _force_linux(monkeypatch)
    _patch_pactl(monkeypatch, lambda: True)

    called = {"n": 0}

    def _track(_kind=None):
        called["n"] += 1
        return {"max_input_channels": 1}

    _patch_sd(monkeypatch, _track)

    from daobidao.recorder import AudioRecorder

    AudioRecorder().probe()
    assert called["n"] == 0, "Linux 路径不应调 query_devices"


# ====================================================================
# pactl 输出解析(单元覆盖各种边界)
# ====================================================================


def _patch_subprocess_run(monkeypatch, fake_run):
    """替换 recorder 模块里 subprocess.run。"""
    monkeypatch.setattr("daobidao.recorder.subprocess.run", fake_run)


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    """造一个像 subprocess.CompletedProcess 的对象。"""

    class _Proc:
        pass

    p = _Proc()
    p.stdout = stdout
    p.returncode = returncode
    p.stderr = stderr
    return p


_PACTL_OUTPUT_BUILTIN_UNPLUGGED_USB_PLUGGED = """\
Source #50
\tState: SUSPENDED
\tName: alsa_input.pci-0000_00_1f.3.analog-stereo
\tDriver: PipeWire
\tPorts:
\t\tanalog-input-front-mic: Front Microphone (type: Mic, priority: 8500, availability group: Legacy 1, not available)
\t\tanalog-input-rear-mic: Rear Microphone (type: Mic, priority: 8200, availability group: Legacy 2, not available)
\tActive Port: analog-input-front-mic
Source #200
\tState: SUSPENDED
\tName: alsa_input.usb-BOYA-mini-2-02.analog-stereo
\tDriver: PipeWire
\tPorts:
\t\tanalog-input-mic: Microphone (type: Mic, priority: 8700, availability unknown)
\tActive Port: analog-input-mic
"""

_PACTL_OUTPUT_ALL_NOT_AVAILABLE = """\
Source #50
\tState: SUSPENDED
\tName: alsa_input.pci-0000_00_1f.3.analog-stereo
\tPorts:
\t\tanalog-input-front-mic: Front Microphone (type: Mic, priority: 8500, availability group: Legacy 1, not available)
\t\tanalog-input-rear-mic: Rear Microphone (type: Mic, priority: 8200, availability group: Legacy 2, not available)
\t\tanalog-input-linein: Line In (type: Line, priority: 8100, availability group: Legacy 3, not available)
\tActive Port: analog-input-front-mic
Source #49
\tState: SUSPENDED
\tName: alsa_output.pci-0000_00_1f.3.iec958-stereo.monitor
\tPorts:
\t\tiec958-stereo-output: Digital Output (S/PDIF) (type: SPDIF, priority: 0, availability unknown)
"""

_PACTL_OUTPUT_NO_INPUTS = """\
Source #49
\tState: SUSPENDED
\tName: alsa_output.pci-0000_00_1f.3.iec958-stereo.monitor
\tPorts:
\t\tiec958-stereo-output: Digital Output (S/PDIF) (type: SPDIF, priority: 0, availability unknown)
"""


def test_pactl_parser_returns_true_when_usb_mic_plugged(monkeypatch):
    """USB 麦的 port 是 availability unknown → 视为可用。"""
    _patch_subprocess_run(
        monkeypatch,
        lambda *_a, **_k: _completed(
            _PACTL_OUTPUT_BUILTIN_UNPLUGGED_USB_PLUGGED
        ),
    )
    from daobidao.recorder import _check_pactl_input_available

    assert _check_pactl_input_available() is True


def test_pactl_parser_returns_false_when_all_not_available(monkeypatch):
    """全部 alsa_input.* port 都 not available → False。"""
    _patch_subprocess_run(
        monkeypatch,
        lambda *_a, **_k: _completed(_PACTL_OUTPUT_ALL_NOT_AVAILABLE),
    )
    from daobidao.recorder import _check_pactl_input_available

    assert _check_pactl_input_available() is False


def test_pactl_parser_returns_false_when_no_input_source(monkeypatch):
    """系统里压根没 alsa_input.* → False(而非抛错)。"""
    _patch_subprocess_run(
        monkeypatch,
        lambda *_a, **_k: _completed(_PACTL_OUTPUT_NO_INPUTS),
    )
    from daobidao.recorder import _check_pactl_input_available

    assert _check_pactl_input_available() is False


def test_pactl_parser_raises_when_pactl_not_found(monkeypatch):
    def _boom(*_a, **_k):
        raise FileNotFoundError("pactl: No such file")

    _patch_subprocess_run(monkeypatch, _boom)
    from daobidao.recorder import (
        PactlUnavailableError,
        _check_pactl_input_available,
    )

    with pytest.raises(PactlUnavailableError) as excinfo:
        _check_pactl_input_available()
    assert "not found" in str(excinfo.value)


def test_pactl_parser_raises_on_nonzero_returncode(monkeypatch):
    _patch_subprocess_run(
        monkeypatch,
        lambda *_a, **_k: _completed(
            "", returncode=1, stderr="Connection refused"
        ),
    )
    from daobidao.recorder import (
        PactlUnavailableError,
        _check_pactl_input_available,
    )

    with pytest.raises(PactlUnavailableError) as excinfo:
        _check_pactl_input_available()
    assert "returncode=1" in str(excinfo.value)


def test_pactl_parser_raises_on_timeout(monkeypatch):
    import subprocess

    def _slow(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd=["pactl"], timeout=0.5)

    _patch_subprocess_run(monkeypatch, _slow)
    from daobidao.recorder import (
        PactlUnavailableError,
        _check_pactl_input_available,
    )

    with pytest.raises(PactlUnavailableError):
        _check_pactl_input_available()


def test_pactl_parser_ignores_monitor_sources(monkeypatch):
    """xxx.monitor 名字的 source 是 sink 回环,不该被算成 input。"""
    output = """\
Source #1
\tName: alsa_output.foo.monitor
\tPorts:
\t\tspeaker-output: Speaker (type: Speaker, priority: 0, available)
"""
    _patch_subprocess_run(monkeypatch, lambda *_a, **_k: _completed(output))
    from daobidao.recorder import _check_pactl_input_available

    # .monitor 不算 input,所以 found_input=False → 返回 False
    assert _check_pactl_input_available() is False


# ====================================================================
# locale:pactl 输出是本地化的,解析器必须锁死在固定 locale 下取
# ====================================================================
#
# 37 轮真机 bug:用户桌面 LANG=zh_CN.UTF-8 时 pactl 把字段名翻成
# 「名称：」「端口：」,解析器一条都匹配不上 → 判成"没有可用输入端口" →
# 麦克风永远探测不到、完全无法录音。
#
# 下面的中文 fixture 是从那台机器(Ubuntu 24.04 / PipeWire / pactl 16.1)
# 上真抓的,不是手写模拟。注意被翻译的只有字段名和 port 的 description,
# `availability unknown` / `not available` 这些判定关键字保持英文。

_PACTL_OUTPUT_ZH = """\
信源 #49
\t状态：RUNNING
\t名称：alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
\t驱动程序：PipeWire
\t端口：
\t\tanalog-output-speaker: 扬声器 (type: 扬声器, priority: 10000, availability group: Legacy 4, availability unknown)
\t活动端口：analog-output-speaker
信源 #50
\t状态：SUSPENDED
\t名称：alsa_input.pci-0000_00_1f.3.analog-stereo
\t驱动程序：PipeWire
\t端口：
\t\tanalog-input-front-mic: 前麦克风 (type: Mic, priority: 8500, availability group: Legacy 1, not available)
\t\tanalog-input-mic: 话筒 (type: Mic, priority: 8700, availability group: Legacy 2, not available)
\t活动端口：analog-input-front-mic
信源 #51
\t状态：SUSPENDED
\t名称：alsa_input.usb-Shenzhen_jiayz_photo_industrial_ltd_BOYA_mini_2-02.analog-stereo
\t驱动程序：PipeWire
\t端口：
\t\tanalog-input-mic: 话筒 (type: Mic, priority: 8700, availability unknown)
\t活动端口：analog-input-mic
"""

# 同一台机器、同一时刻,只是 LC_ALL=C 跑出来的那一份。
_PACTL_OUTPUT_EN = """\
Source #49
\tState: RUNNING
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
\tDriver: PipeWire
\tPorts:
\t\tanalog-output-speaker: Speaker (type: Speaker, priority: 10000, availability group: Legacy 4, availability unknown)
\tActive Port: analog-output-speaker
Source #50
\tState: SUSPENDED
\tName: alsa_input.pci-0000_00_1f.3.analog-stereo
\tDriver: PipeWire
\tPorts:
\t\tanalog-input-front-mic: Front Microphone (type: Mic, priority: 8500, availability group: Legacy 1, not available)
\t\tanalog-input-mic: Microphone (type: Mic, priority: 8700, availability group: Legacy 2, not available)
\tActive Port: analog-input-front-mic
Source #51
\tState: SUSPENDED
\tName: alsa_input.usb-Shenzhen_jiayz_photo_industrial_ltd_BOYA_mini_2-02.analog-stereo
\tDriver: PipeWire
\tPorts:
\t\tanalog-input-mic: Microphone (type: Mic, priority: 8700, availability unknown)
\tActive Port: analog-input-mic
"""


def _fake_localized_pactl(*_a, **kwargs):
    """一个会看 env 的假 pactl:没把 locale 锁成 C 就吐中文。

    这样 locale 是**被测行为的输入**,而不是跑测机器的属性 —— 同一份代码
    在英文机 / 中文机 / CI 上都得出逐字相同的结论。
    """
    env = kwargs.get("env") or {}
    lc = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    if lc.split(".")[0] in ("C", "POSIX"):
        return _completed(_PACTL_OUTPUT_EN)
    return _completed(_PACTL_OUTPUT_ZH)


def test_pactl_parser_immune_to_user_locale(monkeypatch):
    """用户桌面是中文 locale 时也要认出插着的 USB 麦。

    改动前:recorder 不传 env → 假 pactl 吐中文 → 解析器全不匹配 → False。
    """
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setenv("LANGUAGE", "zh_CN:zh")
    monkeypatch.delenv("LC_ALL", raising=False)
    _patch_subprocess_run(monkeypatch, _fake_localized_pactl)

    from daobidao.recorder import _check_pactl_input_available

    assert _check_pactl_input_available() is True


def test_pactl_run_forces_c_locale_without_dropping_env(monkeypatch):
    """传给 pactl 的 env 必须锁 C locale,且保留原有环境变量。

    env= 是整体替换而非叠加:只传 {"LC_ALL": "C"} 会把 XDG_RUNTIME_DIR /
    PULSE_SERVER 掐掉,pactl 直接连不上音频服务器。
    """
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setenv("LANGUAGE", "zh_CN:zh")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    seen: dict = {}

    def _capture(*_a, **kwargs):
        seen.update(kwargs)
        return _completed(_PACTL_OUTPUT_EN)

    _patch_subprocess_run(monkeypatch, _capture)

    from daobidao.recorder import _check_pactl_input_available

    _check_pactl_input_available()

    env = seen.get("env")
    assert env is not None, "必须显式传 env,否则跟着用户 locale 走"
    assert env["LC_ALL"] == "C"
    assert env["LANGUAGE"] == ""
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_pactl_parser_only_understands_english_field_names(monkeypatch):
    """解析器本身只认英文字段名 —— 这是上面那条 locale 锁定的前提。

    不是在保留 bug:把这个前提钉住,将来谁动了 locale 锁定,这条会提醒他
    解析这一侧也得跟着改。
    """
    _patch_subprocess_run(
        monkeypatch, lambda *_a, **_k: _completed(_PACTL_OUTPUT_ZH)
    )
    from daobidao.recorder import _check_pactl_input_available

    assert _check_pactl_input_available() is False


# ====================================================================
# 非 Linux 路径(query_devices)
# ====================================================================


def test_probe_non_linux_succeeds_on_normal_device(monkeypatch):
    _force_non_linux(monkeypatch)
    _patch_sd(
        monkeypatch,
        lambda kind=None: {"name": "fake", "max_input_channels": 1},
    )

    from daobidao.recorder import AudioRecorder

    AudioRecorder().probe()


def test_probe_non_linux_raises_when_query_devices_throws(monkeypatch):
    _force_non_linux(monkeypatch)

    def _boom(kind=None):
        raise _FakePortAudioError("No default input device available")

    _patch_sd(monkeypatch, _boom)

    from daobidao.recorder import AudioRecorder, MicUnavailableError

    with pytest.raises(MicUnavailableError) as excinfo:
        AudioRecorder().probe()
    assert excinfo.value.reason == "probe_failed"
    assert "No default input device" in (excinfo.value.detail or "")


def test_probe_non_linux_raises_when_zero_input_channels(monkeypatch):
    _force_non_linux(monkeypatch)
    _patch_sd(
        monkeypatch,
        lambda kind=None: {"name": "ghost", "max_input_channels": 0},
    )

    from daobidao.recorder import AudioRecorder, MicUnavailableError

    with pytest.raises(MicUnavailableError) as excinfo:
        AudioRecorder().probe()
    assert "max_input_channels=0" in (excinfo.value.detail or "")


def test_probe_non_linux_raises_on_none(monkeypatch):
    _force_non_linux(monkeypatch)
    _patch_sd(monkeypatch, lambda kind=None: None)

    from daobidao.recorder import AudioRecorder, MicUnavailableError

    with pytest.raises(MicUnavailableError):
        AudioRecorder().probe()


def test_probe_non_linux_handles_list_return(monkeypatch):
    _force_non_linux(monkeypatch)
    _patch_sd(
        monkeypatch,
        lambda kind=None: [{"name": "first", "max_input_channels": 2}],
    )

    from daobidao.recorder import AudioRecorder

    AudioRecorder().probe()


def test_probe_non_linux_raises_on_timeout(monkeypatch):
    _force_non_linux(monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def _slow(kind=None):
        started.set()
        release.wait(timeout=2.0)
        return {"max_input_channels": 1}

    _patch_sd(monkeypatch, _slow)

    from daobidao.recorder import AudioRecorder, MicUnavailableError

    rec = AudioRecorder()
    t0 = time.monotonic()
    with pytest.raises(MicUnavailableError) as excinfo:
        rec.probe(timeout=0.05)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.6
    assert "timeout" in (excinfo.value.detail or "")
    release.set()


def test_probe_non_linux_does_not_block_when_query_succeeds_fast(
    monkeypatch,
):
    _force_non_linux(monkeypatch)
    _patch_sd(
        monkeypatch,
        lambda kind=None: {"max_input_channels": 1},
    )

    from daobidao.recorder import AudioRecorder

    rec = AudioRecorder()
    t0 = time.monotonic()
    rec.probe(timeout=1.0)
    assert time.monotonic() - t0 < 0.1
