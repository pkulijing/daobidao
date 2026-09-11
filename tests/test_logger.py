"""logger 模块:路径解析 + configure_logging + 结构化输出。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """还原 root logger handlers + structlog 全局 config + _configured 标志,
    避免 test_logger 用例污染同 session 后续其它测试文件的 logger 输出。

    背景:configure_logging 直接动 root.handlers 和 structlog.configure,
    跑完不还原会让后续 test_qwen3_* 的 logger.info 走进一个被改过的全局
    state,日志去向不可控(实测 → 沉默 / 写到 tmp_path 已删的文件)。
    """
    import daobidao.logger as log_mod

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_configured = log_mod._configured
    for h in root.handlers[:]:
        root.removeHandler(h)
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    structlog.reset_defaults()
    log_mod._configured = saved_configured


def test_get_log_dir_dev_mode(monkeypatch, tmp_path):
    """dev 模式下日志目录落在 repo_root/logs/。"""
    import daobidao.logger as log_mod

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: fake_root,
    )
    assert log_mod.get_log_dir() == fake_root / "logs"
    assert log_mod.get_log_file() == fake_root / "logs" / "daobidao.log"


def test_get_log_dir_macos(monkeypatch):
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: None,
    )
    monkeypatch.setattr(log_mod, "IS_MACOS", True)
    expected = Path(os.path.expanduser("~/Library/Logs/Daobidao"))
    assert log_mod.get_log_dir() == expected


def test_get_log_dir_linux_xdg(monkeypatch, tmp_path):
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: None,
    )
    monkeypatch.setattr(log_mod, "IS_MACOS", False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert log_mod.get_log_dir() == tmp_path / "daobidao"


def test_get_log_dir_linux_xdg_fallback(monkeypatch):
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: None,
    )
    monkeypatch.setattr(log_mod, "IS_MACOS", False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    expected = Path(os.path.expanduser("~/.local/state")) / "daobidao"
    assert log_mod.get_log_dir() == expected


def test_configure_logging_idempotent(monkeypatch, tmp_path):
    """多次调 configure_logging 不应累加 handler。"""
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: tmp_path,
    )
    # 默认 file + 控制台通道两个 handler。再调依旧是 2 个,不累加。
    log_mod.configure_logging("INFO")
    first = len(logging.getLogger().handlers)
    log_mod.configure_logging("DEBUG")
    second = len(logging.getLogger().handlers)
    assert first == second == 2  # file + console
    assert logging.getLogger().level == logging.DEBUG

    # console=False(--quiet):只剩 file handler
    log_mod.configure_logging("INFO", console=False)
    assert len(logging.getLogger().handlers) == 1

    # stderr=True(--verbose):file + 完整 ConsoleRenderer,控制台通道让位
    log_mod.configure_logging("INFO", stderr=True)
    third = len(logging.getLogger().handlers)
    assert third == 2  # file + stderr


def test_log_file_logfmt_format(monkeypatch, tmp_path):
    """文件输出应是 logfmt (key=value),结构化 event + 关键字段都能读到。"""
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: tmp_path,
    )
    log_mod.configure_logging("INFO")

    logger = log_mod.get_logger("test.logfmt")
    logger.info("hotkey_listening", hotkey="KEY_RIGHTCTRL")

    # 刷 handler
    for h in logging.getLogger().handlers:
        h.flush()

    content = (tmp_path / "logs" / "daobidao.log").read_text(encoding="utf-8")
    assert "event='hotkey_listening'" in content
    assert "hotkey='KEY_RIGHTCTRL'" in content
    assert "level='info'" in content
    assert "timestamp=" in content


def test_log_file_rotation(monkeypatch, tmp_path):
    """写够字节数后应当滚出 .log.1。"""
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: tmp_path,
    )
    # 收紧 maxBytes,让少量日志就能触发轮转
    monkeypatch.setattr(log_mod, "_MAX_BYTES", 200)
    log_mod.configure_logging("INFO")

    logger = log_mod.get_logger("test.rotation")
    # 每条 logfmt 行大概 80-120 字节,写 20 条稳触发
    for i in range(20):
        logger.info("event_x", i=i, payload="a" * 40)
    for h in logging.getLogger().handlers:
        h.flush()

    log_dir = tmp_path / "logs"
    main = log_dir / "daobidao.log"
    rotated = log_dir / "daobidao.log.1"
    assert main.exists()
    assert rotated.exists()


def test_exception_goes_to_log(monkeypatch, tmp_path):
    """logger.exception() 应把 traceback 写到文件。"""
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: tmp_path,
    )
    log_mod.configure_logging("INFO")

    logger = log_mod.get_logger("test.exc")
    try:
        raise ValueError("boom-marker-123")
    except ValueError:
        logger.exception("recognize_failed")
    for h in logging.getLogger().handlers:
        h.flush()

    content = (tmp_path / "logs" / "daobidao.log").read_text(encoding="utf-8")
    assert "recognize_failed" in content
    assert "ValueError" in content
    assert "boom-marker-123" in content


def test_launchd_log_file_path(monkeypatch, tmp_path):
    """plist StandardErrorPath 指向 get_launchd_log_file()。"""
    import daobidao.logger as log_mod

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: tmp_path,
    )
    assert (
        log_mod.get_launchd_log_file()
        == tmp_path / "logs" / "daobidao-launchd.log"
    )


# ====================================================================
# 控制台通道(37 轮)
# ====================================================================
#
# 34 轮把 terminal log 整体静默后,终端上一条应用自己的输出都没有 ——
# 用户看到的唯一一行是 modelscope 打的 "Downloading 9 files ...",
# 之后永远沉默,首次下载 7 分钟里看起来完全就是卡死。
#
# 修法不是回滚那次静默(不想让终端持续刷结构化 INFO log 是对的),而是
# 加一条独立的控制台通道:WARNING 以上一律放行,INFO 只放行启动里程碑
# 白名单;纯文本、不带时间戳 / logger 名 / key=value。


def _configure_with_console(log_mod, tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: tmp_path,
    )
    log_mod.configure_logging("INFO", **kwargs)


def _read_log_file(tmp_path):
    for h in logging.getLogger().handlers:
        h.flush()
    return (tmp_path / "logs" / "daobidao.log").read_text(encoding="utf-8")


def test_console_channel_on_by_default(monkeypatch, tmp_path):
    """默认挂 file + console 两个 handler。"""
    import daobidao.logger as log_mod

    _configure_with_console(log_mod, tmp_path, monkeypatch)
    kinds = [type(h).__name__ for h in logging.getLogger().handlers]
    assert "RotatingFileHandler" in kinds
    assert "StreamHandler" in kinds
    assert len(kinds) == 2


def test_console_shows_allowlisted_startup_milestone(
    monkeypatch, tmp_path, capsys
):
    """白名单内的 INFO 上终端,且是纯文本 —— 无时间戳 / event= / logger 名。"""
    import daobidao.logger as log_mod

    _configure_with_console(log_mod, tmp_path, monkeypatch)
    log_mod.get_logger("daobidao.__main__").info(
        "ready", message="就绪！按住热键开始说话", exit_hint="Ctrl+C 退出"
    )

    err = capsys.readouterr().err
    assert "就绪！按住热键开始说话" in err
    assert "timestamp" not in err
    assert "event=" not in err
    assert "daobidao.__main__" not in err


def test_console_hides_non_allowlisted_info(monkeypatch, tmp_path, capsys):
    """白名单外的 INFO 不上终端,但照常进文件日志。"""
    import daobidao.logger as log_mod

    _configure_with_console(log_mod, tmp_path, monkeypatch)
    log_mod.get_logger("daobidao.stt").info("qwen3_warmup_done", elapsed_ms=42)

    assert "qwen3_warmup_done" not in capsys.readouterr().err
    assert "qwen3_warmup_done" in _read_log_file(tmp_path)


def test_console_shows_warning_not_on_allowlist(monkeypatch, tmp_path, capsys):
    """WARNING 以上一律放行 —— 白名单管不着。

    这正是 37 轮那个麦克风 bug 的防线:mic_offline 是 warning,按这条规则
    用户第一次按热键就会在终端看到它,而不是对着毫无反应的键发呆。
    """
    import daobidao.logger as log_mod

    _configure_with_console(log_mod, tmp_path, monkeypatch)
    log_mod.get_logger("daobidao.__main__").warning(
        "mic_offline", message="麦克风离线", reason="probe_failed"
    )

    err = capsys.readouterr().err
    assert "麦克风离线" in err


def test_console_warning_without_message_still_readable(
    monkeypatch, tmp_path, capsys
):
    """warning 没带 message 字段时,退回事件名 + 关键字段,不能打成空行。"""
    import daobidao.logger as log_mod

    _configure_with_console(log_mod, tmp_path, monkeypatch)
    log_mod.get_logger("daobidao.__main__").warning(
        "stream_kv_overflow_unexpected", cur_len=1200
    )

    err = capsys.readouterr().err
    assert "stream_kv_overflow_unexpected" in err
    assert "1200" in err


def test_console_disabled_by_quiet(monkeypatch, tmp_path, capsys):
    """console=False(--quiet)时终端零输出,文件日志照写。"""
    import daobidao.logger as log_mod

    _configure_with_console(log_mod, tmp_path, monkeypatch, console=False)
    log = log_mod.get_logger("daobidao.__main__")
    log.info("ready", message="就绪！按住热键开始说话")
    log.warning("mic_offline", message="麦克风离线")

    assert capsys.readouterr().err == ""
    content = _read_log_file(tmp_path)
    assert "event='ready'" in content
    assert "event='mic_offline'" in content


def test_verbose_does_not_duplicate_console_channel(
    monkeypatch, tmp_path, capsys
):
    """--verbose 走完整 ConsoleRenderer,不再叠加控制台通道(同一条打两遍)。"""
    import daobidao.logger as log_mod

    _configure_with_console(log_mod, tmp_path, monkeypatch, stderr=True)
    assert len(logging.getLogger().handlers) == 2  # file + verbose stderr

    log_mod.get_logger("daobidao.__main__").info(
        "ready", message="就绪！按住热键开始说话"
    )
    err = capsys.readouterr().err
    assert err.count("就绪！按住热键开始说话") == 1
