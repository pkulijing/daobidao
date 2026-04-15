"""测试 macOS 自启动 plist 生成 / 启用 / 禁用。

针对 src/whisper_input/backends/autostart_macos.py。

注意:这个文件**纯 stdlib,不 import pyobjc**,所以 linux CI 上也能跑。

测试隔离策略:
- AUTOSTART_DIR / AUTOSTART_FILE 用 monkeypatch 指向 tmp_path
- subprocess.run(launchctl ...) 用 monkeypatch 替换成记录调用的 fake
- sys.prefix 用 monkeypatch 改成 tmp 目录,验证 _program_arguments
  能挑出 venv 里的 console script
"""

import plistlib
import sys

from whisper_input.backends import autostart_macos as am


def test_xml_escape():
    assert am._xml_escape("a&b<c>") == "a&amp;b&lt;c&gt;"
    assert am._xml_escape("plain") == "plain"


def test_program_arguments_prefers_venv_script(tmp_path, monkeypatch):
    """sys.prefix/bin/whisper-input 存在 → 返回它,不退回 -m。"""
    fake_prefix = tmp_path / "venv"
    (fake_prefix / "bin").mkdir(parents=True)
    script = fake_prefix / "bin" / "whisper-input"
    script.write_text("#!/bin/sh\n")

    monkeypatch.setattr(sys, "prefix", str(fake_prefix))
    assert am._program_arguments() == [str(script)]


def test_program_arguments_falls_back_to_module(tmp_path, monkeypatch):
    """sys.prefix 下没有 console script → 退回 [sys.executable, -m, ...]。"""
    monkeypatch.setattr(sys, "prefix", str(tmp_path))  # 没有 bin/
    args = am._program_arguments()
    assert args == [sys.executable, "-m", "whisper_input"]


def test_build_plist_is_valid_and_correct(tmp_path, monkeypatch):
    """_build_plist 输出能被 stdlib plistlib 解析,字段值正确。"""
    # 让 _program_arguments 走 fallback 路径,避免依赖 sys.prefix
    monkeypatch.setattr(sys, "prefix", str(tmp_path))

    plist_xml = am._build_plist()
    parsed = plistlib.loads(plist_xml.encode("utf-8"))
    assert parsed["Label"] == "com.whisper-input"
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is False
    assert parsed["ProcessType"] == "Interactive"
    assert parsed["ProgramArguments"] == [
        sys.executable,
        "-m",
        "whisper_input",
    ]


def test_set_autostart_true_writes_file(tmp_path, monkeypatch):
    """启用后 plist 文件被写到 AUTOSTART_FILE 指向的位置,内容是 _build_plist。"""
    target_dir = tmp_path / "LaunchAgents"
    target_file = target_dir / "com.whisper-input.plist"
    monkeypatch.setattr(am, "AUTOSTART_DIR", str(target_dir))
    monkeypatch.setattr(am, "AUTOSTART_FILE", str(target_file))

    assert not am.is_autostart_enabled()
    am.set_autostart(True)
    assert target_file.is_file()
    assert am.is_autostart_enabled()

    # 内容是合法 plist
    parsed = plistlib.loads(target_file.read_bytes())
    assert parsed["Label"] == "com.whisper-input"


def test_set_autostart_false_removes_file_and_calls_launchctl(
    tmp_path, monkeypatch
):
    """禁用后文件被删,且 launchctl bootout 被调用一次。"""
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    target_file = target_dir / "com.whisper-input.plist"
    target_file.write_text("<plist></plist>")  # 假装已经启用过

    monkeypatch.setattr(am, "AUTOSTART_DIR", str(target_dir))
    monkeypatch.setattr(am, "AUTOSTART_FILE", str(target_file))

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(am.subprocess, "run", fake_run)

    assert am.is_autostart_enabled()
    am.set_autostart(False)

    assert not target_file.exists()
    assert not am.is_autostart_enabled()
    # launchctl bootout 被调用过
    assert len(calls) == 1
    assert calls[0][0] == "launchctl"
    assert calls[0][1] == "bootout"
    assert "com.whisper-input" in calls[0][2]


def test_set_autostart_false_when_already_disabled(
    tmp_path, monkeypatch
):
    """重复禁用不应该报错。"""
    target_dir = tmp_path / "LaunchAgents"
    target_file = target_dir / "com.whisper-input.plist"
    monkeypatch.setattr(am, "AUTOSTART_DIR", str(target_dir))
    monkeypatch.setattr(am, "AUTOSTART_FILE", str(target_file))
    monkeypatch.setattr(am.subprocess, "run", lambda *a, **kw: None)

    am.set_autostart(False)  # 不应该抛
    assert not target_file.exists()
