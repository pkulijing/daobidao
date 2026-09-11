"""首次下载的「别急,它在下」提示(37 轮)。

modelscope 的 ``snapshot_download`` 并行下载、只打一个**按文件计数**的聚合
进度条(9 个文件),没有按字节的分文件条。真下载时最大那个
``decoder.int8.onnx``(756 MB)会让这个条在同一格上停数分钟不动 —— 这就是
用户报「卡在模型下载不结束」的直接来源。

所以在 ``snapshot_download`` 之前起一个计时器,超过阈值还没返回就打一条
``qwen3_download_slow``(在 logger 的控制台白名单里),返回后 cancel。

**用计时器而不是「检查缓存目录在不在」**:modelscope 的缓存根路径随版本
变过(本项目 CLAUDE.md 写的 ``~/.cache/modelscope/hub/``,实机上是
``~/.cache/modelscope/models/``),自己推路径是又一个会随环境漂移的假设;
计时器只依赖「这次调用有没有很快返回」,任何版本、任何缓存布局下都成立。
"""

from __future__ import annotations

import sys
import time
import types

import pytest


class _SentinelError(Exception):
    """让 load() 在 snapshot_download 之后立刻中断,不去构建真的 ONNX session。"""


class _RecordingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def _record(self, event, **kw):
        self.events.append((event, kw))

    info = _record
    debug = _record
    warning = _record
    error = _record
    exception = _record

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


@pytest.fixture
def asr_mod():
    from daobidao.stt.qwen3 import qwen3_asr

    return qwen3_asr


def _install_fake_modelscope(monkeypatch, download_fn):
    """把 ``from modelscope import snapshot_download`` 换成我们的桩。

    load() 里是函数内 import,所以往 sys.modules 塞一个假模块即可。
    """
    fake = types.ModuleType("modelscope")
    fake.snapshot_download = download_fn
    monkeypatch.setitem(sys.modules, "modelscope", fake)


def _make_stt(asr_mod, monkeypatch, hint_after: float):
    monkeypatch.setattr(asr_mod, "_SLOW_DOWNLOAD_HINT_S", hint_after)
    rec = _RecordingLogger()
    monkeypatch.setattr(asr_mod, "logger", rec)
    stt = asr_mod.Qwen3ASRSTT(variant="0.6B")
    return stt, rec


def test_slow_download_emits_hint(asr_mod, monkeypatch):
    """下载慢过阈值 → 打一条 qwen3_download_slow,带人话文案。"""
    stt, rec = _make_stt(asr_mod, monkeypatch, hint_after=0.02)

    def _slow(*_a, **_k):
        time.sleep(0.15)
        raise _SentinelError

    _install_fake_modelscope(monkeypatch, _slow)

    with pytest.raises(_SentinelError):
        stt.load()

    assert "qwen3_download_slow" in rec.names()
    payload = dict(
        rec.events[[e for e, _ in rec.events].index("qwen3_download_slow")][1]
    )
    assert payload["variant"] == "0.6B"
    # message 是给终端看的人话,不能是空的 / 不能还是模板占位符
    assert payload["message"]
    assert "{" not in payload["message"]


def test_fast_download_emits_no_hint(asr_mod, monkeypatch):
    """缓存命中、秒回 → 计时器被 cancel,不打提示。"""
    stt, rec = _make_stt(asr_mod, monkeypatch, hint_after=0.5)

    def _fast(*_a, **_k):
        raise _SentinelError

    _install_fake_modelscope(monkeypatch, _fast)

    with pytest.raises(_SentinelError):
        stt.load()

    # 等过阈值,确认计时器真的被取消而不是只是还没到点
    time.sleep(0.6)
    assert "qwen3_download_slow" not in rec.names()


def test_hint_event_is_on_console_allowlist():
    """这条提示必须能上终端,否则等于没加。"""
    from daobidao.logger import _CONSOLE_INFO_EVENTS

    assert "qwen3_download_slow" in _CONSOLE_INFO_EVENTS
