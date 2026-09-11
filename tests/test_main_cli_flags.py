"""`main()` 的 CLI 开关接线 —— 37 轮 review 补。

这块此前零覆盖(CLAUDE.md 里 `__main__.main()` 一直列在"Not tested"),而
`--quiet` 恰恰是本轮新加的开关。review 抓到:`--init` 分支在读配置之前就
``return``,而应用 CLI flag 的那次 ``configure_logging`` 在它后面,于是
``daobidao --init --quiet`` 仍会往终端打四条里程碑。

这里只覆盖"开关有没有接上"这一层,不碰完整启动编排。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def _isolated_logging(monkeypatch, tmp_path):
    """把日志目录挪进 tmp、语言钉死成 zh,并在用例结束后还原全局状态。

    语言必须显式指定:``--init`` 分支在读配置之前就 return,不会调
    ``set_language``,于是它打出来的文案跟着**进程里上一次**设置的语言走。
    实测 `test_i18n.py` 跑完会把全局语言留在 fr,本文件断言中文文案就会红 ——
    同一份代码换个执行顺序得出不同结论,正是"测试继承了宿主状态"。
    """
    import logging

    import daobidao.i18n as i18n_mod
    import daobidao.logger as log_mod

    saved_lang = i18n_mod.get_language()
    i18n_mod.set_language("zh")

    monkeypatch.setattr(
        "daobidao.config_manager._find_project_root",
        lambda: tmp_path,
    )
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    saved_configured = log_mod._configured
    for h in root.handlers[:]:
        root.removeHandler(h)
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in saved:
        root.addHandler(h)
    root.setLevel(saved_level)
    import structlog

    structlog.reset_defaults()
    log_mod._configured = saved_configured
    i18n_mod.set_language(saved_lang)


def _run_init(monkeypatch, tmp_path, extra_argv: list[str]) -> MagicMock:
    """跑 `daobidao --init [extra]`,返回被打桩的 stt。"""
    fake_stt = MagicMock()
    fake_stt.variant = "0.6B"
    monkeypatch.setattr(
        "daobidao.__main__.create_stt_engine", lambda cfg: fake_stt
    )
    if sys.platform == "darwin":
        monkeypatch.setattr(
            "daobidao.backends.app_bundle_macos.install_app_bundle",
            lambda *a, **kw: None,
        )
    monkeypatch.setattr(
        "daobidao._legacy_migration.migrate_once", lambda *a, **kw: None
    )
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(
        sys, "argv", ["daobidao", "--init", "-c", str(cfg), *extra_argv]
    )

    from daobidao.__main__ import main

    main()
    return fake_stt


def test_init_prints_milestones_by_default(
    monkeypatch, tmp_path, capsys, _isolated_logging
):
    """默认 --init 会把初始化里程碑打到终端(这是本轮想要的反馈)。"""
    stt = _run_init(monkeypatch, tmp_path, [])

    stt.load.assert_called_once()
    err = capsys.readouterr().err
    assert "叨逼叨" in err or "初始化" in err


def test_init_quiet_prints_nothing(
    monkeypatch, tmp_path, capsys, _isolated_logging
):
    """--init --quiet 必须终端零输出 —— cli.quiet_help 就是这么承诺的。"""
    stt = _run_init(monkeypatch, tmp_path, ["--quiet"])

    stt.load.assert_called_once()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
