"""测试两个平台的 input_method shell-out 顺序。

针对 src/daobidao/backends/input_macos.py 和 input_linux.py。

测试策略:
- monkeypatch subprocess.run 成记录调用的 fake,验证调用顺序和参数
- macOS 的 pynput Controller 由 conftest 注入的 fake 提供,fake 会把
  press / release 记到 .calls 列表里
"""

import subprocess
import types

import pytest
from pynput.keyboard import Key

from daobidao.backends import input_linux as il
from daobidao.backends import input_macos as im


class _RunRecorder:
    """记录 subprocess.run 调用的 fake。

    每条记录是 (cmd, input_bytes) 元组。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs.get("input")))

        class Result:
            stdout = b""
            returncode = 0

        return Result()


# --- macOS ---


def test_macos_empty_text_no_subprocess(monkeypatch):
    rec = _RunRecorder()
    monkeypatch.setattr(im.subprocess, "run", rec)
    im.type_text("")
    assert rec.calls == []


def test_macos_clipboard_paste_sequence(monkeypatch):
    rec = _RunRecorder()
    monkeypatch.setattr(im.subprocess, "run", rec)
    # 不真等 50ms / 100ms 的 sleep
    monkeypatch.setattr(im.time, "sleep", lambda _: None)
    # 清掉 fake Controller 的历史调用
    im._keyboard.calls.clear()

    im.type_text("hello world")

    # 至少 3 次 subprocess(pbpaste 读 + pbcopy 写 + pbcopy 还原)
    cmds = [c[0][0] for c in rec.calls]
    assert "pbpaste" in cmds
    assert cmds.count("pbcopy") >= 1

    # 找到写入新文本的那次 pbcopy 调用
    paste_payloads = [
        payload
        for cmd, payload in rec.calls
        if cmd[0] == "pbcopy" and payload is not None
    ]
    assert b"hello world" in paste_payloads

    # Cmd+V 的 4 次按键调用顺序
    from pynput.keyboard import Key

    assert im._keyboard.calls == [
        ("press", Key.cmd),
        ("press", "v"),
        ("release", "v"),
        ("release", Key.cmd),
    ]


# --- Linux ---


def test_linux_empty_text_no_subprocess(monkeypatch):
    rec = _RunRecorder()
    monkeypatch.setattr(il.subprocess, "run", rec)
    il.type_text("")
    assert rec.calls == []


def test_linux_clipboard_writes_both_selections_and_pastes(
    monkeypatch,
):
    rec = _RunRecorder()
    monkeypatch.setattr(il.subprocess, "run", rec)
    monkeypatch.setattr(il.time, "sleep", lambda _: None)

    il.type_text("中文测试")

    cmds = [c[0] for c in rec.calls]
    # 必须同时写 clipboard 和 primary 两个 selection
    assert any(c[:3] == ["xclip", "-selection", "clipboard"] for c in cmds)
    assert any(c[:3] == ["xclip", "-selection", "primary"] for c in cmds)
    # 必须用 xdotool key shift+Insert 触发粘贴
    assert any(
        c
        == [
            "xdotool",
            "key",
            "--clearmodifiers",
            "shift+Insert",
        ]
        for c in cmds
    )

    # 写入两个 selection 时 input payload 是 utf-8 编码
    payloads = [
        payload
        for cmd, payload in rec.calls
        if cmd[0] == "xclip" and payload is not None
    ]
    assert any(p == "中文测试".encode() for p in payloads)


# --- 失败路径:粘贴步骤抛异常时仍须还原用户原剪贴板 ---


class _RunStub:
    """可指定某条命令失败的 subprocess.run fake。

    ``fail_on`` 为空则全部成功;命中该前缀的调用抛 CalledProcessError,
    模拟 ``check=True`` 下的非零退出。
    """

    def __init__(self, original: bytes = b"", fail_on=None) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.original = original
        self.fail_on = fail_on

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append((cmd, kwargs.get("input")))
        if self.fail_on and cmd[: len(self.fail_on)] == self.fail_on:
            raise subprocess.CalledProcessError(1, cmd)
        return types.SimpleNamespace(stdout=self.original, returncode=0)


@pytest.mark.parametrize(
    "fail_on",
    [
        ["xclip", "-selection", "primary"],
        ["xdotool"],
    ],
)
def test_linux_restores_clipboard_even_when_paste_fails(monkeypatch, fail_on):
    """写 selection / 送键中途失败,也必须把用户原剪贴板还回去。

    失败要抛出去让调用方记日志,但「还原原剪贴板」是收尾动作,不能因为
    前面抛了异常就被跳过 —— 那时 CLIPBOARD 已被识别结果覆盖,而唯一一份
    原内容存在局部变量里,随栈帧一起消失,用户无从恢复。
    """
    stub = _RunStub(original=b"USER-CLIPBOARD", fail_on=fail_on)
    monkeypatch.setattr(il.subprocess, "run", stub)
    monkeypatch.setattr(il.time, "sleep", lambda _: None)

    with pytest.raises(subprocess.CalledProcessError):
        il.type_text("中文测试")

    assert any(
        cmd[:3] == ["xclip", "-selection", "clipboard"]
        and payload == b"USER-CLIPBOARD"
        for cmd, payload in stub.calls
    ), "抛异常前必须把原剪贴板还原回去"


def test_macos_releases_modifier_when_keystroke_fails(monkeypatch):
    """送键中途抛异常时,Cmd 必须被释放掉。

    Cmd 按下后若中途抛出,修饰键会一直保持「按下」状态,此后用户敲的每个
    键都带 Cmd(w 关窗口、q 退应用),而异常已被调用方吞掉、界面毫无提示。
    """
    stub = _RunStub(original=b"USER-CLIPBOARD")
    monkeypatch.setattr(im.subprocess, "run", stub)
    monkeypatch.setattr(im.time, "sleep", lambda _: None)
    im._keyboard.calls.clear()

    real_press = im._keyboard.press

    def _press(key):
        real_press(key)
        if key == "v":
            raise RuntimeError("send key failed")

    monkeypatch.setattr(im._keyboard, "press", _press)

    with pytest.raises(RuntimeError):
        im.type_text("hello")

    released = [key for kind, key in im._keyboard.calls if kind == "release"]
    assert Key.cmd in released, "抛异常前必须把 Cmd 释放掉"


def test_macos_restores_clipboard_even_when_paste_fails(monkeypatch):
    """Cmd+V 送键失败也要还原剪贴板。

    此时 pbcopy 已经把识别结果写进 pasteboard,跳过还原就等于把用户原
    剪贴板永久顶掉。
    """
    stub = _RunStub(original=b"USER-CLIPBOARD")
    monkeypatch.setattr(im.subprocess, "run", stub)
    monkeypatch.setattr(im.time, "sleep", lambda _: None)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("accessibility revoked")

    monkeypatch.setattr(im._keyboard, "press", _boom)

    with pytest.raises(RuntimeError):
        im.type_text("hello")

    assert any(
        cmd[0] == "pbcopy" and payload == b"USER-CLIPBOARD"
        for cmd, payload in stub.calls
    ), "抛异常前必须把原剪贴板还原回去"
