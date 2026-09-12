"""``--verbose`` 的日志级别语义(Fix C)。

``--verbose`` 的文档承诺是「完整 ConsoleRenderer 全量输出」,而 ``config``
里的 ``log_level`` 又有一票否决权。要命的是 ``ConfigManager.config`` 是
``DEFAULT_CONFIG`` 深合并出来的视图:默认值 ``"INFO"`` **永远在**。于是
按 ``config.get("log_level")`` 判断「用户设没设」,``--verbose`` 永远升不到
DEBUG —— 代码里有这个分支、README 里写着这个行为,但它一次都没生效过。

本文件盯住三件事:

1. 文件没写 ``log_level`` → ``--verbose`` 升 DEBUG
2. 文件写了**非默认**级别 → 以文件为准(含不带 --verbose 的情况)
3. 文件写着默认值 ``INFO``(就是 ``config.example.yaml`` 被拷成用户配置的
   情形)→ 不算显式设置,``--verbose`` 仍然升 DEBUG
"""

from __future__ import annotations

from importlib.resources import files

import pytest


@pytest.fixture
def resolve():
    from daobidao.__main__ import _resolve_log_level

    return _resolve_log_level


def _manager(tmp_path, body: str | None):
    """按给定 YAML 内容造一个 ConfigManager(显式路径,不走 example 拷贝)。"""
    from daobidao.config_manager import ConfigManager

    path = tmp_path / "config.yaml"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    return ConfigManager(str(path))


def test_file_without_log_level_verbose_upgrades_to_debug(tmp_path, resolve):
    """核心回归:合并视图里永远有默认值 INFO,不能据此认定用户设过。"""
    mgr = _manager(tmp_path, "engine: qwen3\n")

    assert mgr.config.get("log_level") == "INFO"  # 合并视图里就是有
    assert "log_level" not in mgr.file_config  # 但用户文件里没有

    assert resolve(mgr, verbose=False) == "INFO"
    assert resolve(mgr, verbose=True) == "DEBUG"


def test_example_config_value_does_not_block_verbose(tmp_path, resolve):
    """config.example.yaml 里写着 log_level: INFO —— 首装用户不该因此失效。"""
    example = files("daobidao.assets").joinpath("config.example.yaml")
    mgr = _manager(tmp_path, example.read_text(encoding="utf-8"))

    assert mgr.file_config.get("log_level") == "INFO"
    assert resolve(mgr, verbose=True) == "DEBUG"


def test_explicit_non_default_level_wins(tmp_path, resolve):
    """文件显式写了非默认级别 → 压过 --verbose。"""
    mgr = _manager(tmp_path, "log_level: WARNING\n")

    assert resolve(mgr, verbose=True) == "WARNING"
    assert resolve(mgr, verbose=False) == "WARNING"


def test_explicit_debug_level_used_without_verbose(tmp_path, resolve):
    """不带 --verbose 也能靠配置拿到 DEBUG。"""
    mgr = _manager(tmp_path, "log_level: DEBUG\n")

    assert resolve(mgr, verbose=False) == "DEBUG"


def test_missing_config_file_is_treated_as_unset(tmp_path, resolve):
    """配置文件还不存在(首启,example 尚未落盘)→ 等同未设置。"""
    mgr = _manager(tmp_path, None)

    assert mgr.file_config == {}
    assert resolve(mgr, verbose=True) == "DEBUG"


def test_file_config_is_isolated_copy(tmp_path):
    """file_config 是副本:外部改它不能污染 manager 内部状态。"""
    mgr = _manager(tmp_path, "log_level: DEBUG\n")

    snapshot = mgr.file_config
    snapshot["log_level"] = "ERROR"
    snapshot["injected"] = True

    assert mgr.file_config == {"log_level": "DEBUG"}
