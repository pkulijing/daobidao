"""``Qwen3ASRSTT.load()`` 的本地缓存短路(Fix B)。

背景:改之前每次 ``load()`` 都无条件调 ``modelscope.snapshot_download()``,
即使 942 MB 的模型就在磁盘上 —— 于是启动要先联网确认一次 revision。断网、
或者系统代理配错(GNOME 残留 HTTPS 代理),启动就卡在那里甚至直接崩。

改法:先问 modelscope 一次 ``local_files_only=True``(只读本地索引、零网络
请求),命中就直接用**它自己返回的** cache root。

两条容易做错的地方,本文件各盯一条:

1. ``modelscope_hub`` 的 local_files_only 分支只检查「目录非空」
   (``any(output_dir.iterdir())``),所以「只有另一个 variant」的目录也会被
   它当成命中 —— 必须自己按 variant 复核文件清单,否则会带着不完整的缓存
   一路走到构造 runner 才炸。
2. 它命中时必打一条「confirm the cached file is for revision」的 WARNING,
   会打到用户终端上,所以只在探测期间压掉、之后必须恢复。

这条路只做加速,不承担正确性:任何失败都要落回原来的联网路径。
"""

from __future__ import annotations

import logging
import sys
import types

import pytest


class CacheMissError(Exception):
    """让探测失败(模拟旧版 modelscope 不认这个参数 / 本地没有)。"""


@pytest.fixture
def asr_mod():
    from daobidao.stt.qwen3 import qwen3_asr

    return qwen3_asr


@pytest.fixture
def recording_logger(asr_mod, monkeypatch):
    """记录事件名的假 logger。"""

    class _Rec:
        def __init__(self):
            self.events: list[str] = []

        def _record(self, event, **kw):
            self.events.append(event)

        info = _record
        debug = _record
        warning = _record
        error = _record
        exception = _record

    rec = _Rec()
    monkeypatch.setattr(asr_mod, "logger", rec)
    return rec


def _install_fake_modelscope(monkeypatch, download_fn):
    """替换 ``from modelscope import snapshot_download``(load 里是函数内 import)。"""
    fake = types.ModuleType("modelscope")
    fake.snapshot_download = download_fn
    monkeypatch.setitem(sys.modules, "modelscope", fake)


def _make_cache_dir(asr_mod, root, variant="0.6B"):
    """铺一份「完整」的假缓存目录。"""
    for rel in asr_mod._required_cache_files(variant):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return root


def test_probe_returns_root_and_never_hits_network(
    asr_mod, tmp_path, monkeypatch
):
    """缓存完整 → 返回 modelscope 给的路径,且只用 local_files_only 问一次。"""
    root = _make_cache_dir(asr_mod, tmp_path / "snapshots" / "master")
    calls: list[dict] = []

    def fake(repo_id, **kw):
        calls.append(kw)
        if not kw.get("local_files_only"):
            raise AssertionError("缓存命中时不该走联网分支")
        return str(root)

    _install_fake_modelscope(monkeypatch, fake)

    stt = asr_mod.Qwen3ASRSTT(variant="0.6B")
    assert stt._probe_local_cache(["tokenizer/*"]) == root
    assert calls == [
        {"allow_patterns": ["tokenizer/*"], "local_files_only": True}
    ]


def test_probe_miss_falls_back(asr_mod, monkeypatch):
    """探测抛异常(旧版 modelscope / 本地没有)→ 返回 None,交给联网路径。"""

    def fake(repo_id, **kw):
        raise CacheMissError

    _install_fake_modelscope(monkeypatch, fake)

    stt = asr_mod.Qwen3ASRSTT(variant="0.6B")
    assert stt._probe_local_cache(["tokenizer/*"]) is None


def test_probe_rejects_nonempty_dir_missing_this_variant(
    asr_mod, tmp_path, monkeypatch
):
    """目录非空但只有另一个 variant → 不算命中。

    这条正是 modelscope_hub 的漏洞:它只检查目录非空就返回。1.7B 没下过而
    0.6B 下过时,不自己复核就会拿到一个没有 model_1.7B 的 cache root。
    """
    root = _make_cache_dir(asr_mod, tmp_path / "snapshots" / "master")
    monkeypatch.setattr(
        asr_mod,
        "_required_cache_files",
        lambda variant: ("model_1.7B/decoder.int8.onnx",),
    )

    def fake(repo_id, **kw):
        return str(root)

    _install_fake_modelscope(monkeypatch, fake)

    stt = asr_mod.Qwen3ASRSTT(variant="1.7B")
    assert stt._probe_local_cache(["tokenizer/*"]) is None


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


# 两代 modelscope 打这条 WARNING 的 logger 名与措辞都不同:
# 老版本(<1.40,含锁定的 1.35.4)走自带 stderr handler、propagate=False 的
# "modelscope" logger,不经我们的控制台通道,--quiet 也压不住。
@pytest.mark.parametrize(
    ("logger_name", "message"),
    [
        (
            "modelscope",
            "We can not confirm the cached file is for revision: master",
        ),
        (
            "modelscope_hub.download",
            "Cannot confirm the cached file is for revision: master",
        ),
    ],
)
def test_probe_mutes_revision_warning_then_restores(
    asr_mod, tmp_path, monkeypatch, logger_name, message
):
    """探测期间只压掉 revision WARNING,其它告警照常,结束后不留 filter。"""
    root = _make_cache_dir(asr_mod, tmp_path / "snapshots" / "master")
    target = logging.getLogger(logger_name)
    capture = _Capture()
    target.addHandler(capture)
    original_filters = list(target.filters)
    original_level = target.level

    def fake(repo_id, **kw):
        # modelscope 的 get_logger() 每次调用都会把级别重置回 INFO,
        # 靠调级别压制不可靠 —— 这里模拟这一重置
        target.setLevel(logging.INFO)
        target.warning(message)
        target.warning("unrelated warning")
        return str(root)

    _install_fake_modelscope(monkeypatch, fake)

    stt = asr_mod.Qwen3ASRSTT(variant="0.6B")
    try:
        assert stt._probe_local_cache(["tokenizer/*"]) == root
        assert capture.messages == ["unrelated warning"]
        assert target.filters == original_filters  # 用完必须摘掉

        target.warning(message)  # 探测之外不再拦
        assert capture.messages[-1] == message
    finally:
        target.removeHandler(capture)
        target.setLevel(original_level)


def test_load_uses_cache_and_skips_network(
    asr_mod, tmp_path, monkeypatch, recording_logger
):
    """端到端:缓存命中时 load() 不联网,并打 qwen3_cache_hit。"""
    root = _make_cache_dir(asr_mod, tmp_path / "snapshots" / "master")
    network_calls: list[dict] = []

    def fake(repo_id, **kw):
        if kw.get("local_files_only"):
            return str(root)
        network_calls.append(kw)
        return str(root)

    _install_fake_modelscope(monkeypatch, fake)

    # 别真的去建 ONNX session / 跑 warmup
    monkeypatch.setattr(asr_mod, "Qwen3ONNXRunner", lambda model_dir: object())
    monkeypatch.setattr(asr_mod, "Qwen3Tokenizer", lambda tok_dir: object())
    monkeypatch.setattr(asr_mod.Qwen3ASRSTT, "_warmup", lambda self: None)

    stt = asr_mod.Qwen3ASRSTT(variant="0.6B")
    stt.load()

    assert stt.cache_root == root
    assert network_calls == []
    assert "qwen3_cache_hit" in recording_logger.events
    assert "qwen3_snapshot_start" not in recording_logger.events


def test_load_falls_back_to_network_on_probe_miss(
    asr_mod, tmp_path, monkeypatch, recording_logger
):
    """探测未命中 → 照旧走联网下载,并打 qwen3_snapshot_start / _done。"""
    root = _make_cache_dir(asr_mod, tmp_path / "snapshots" / "master")
    network_calls: list[dict] = []

    def fake(repo_id, **kw):
        if kw.get("local_files_only"):
            raise CacheMissError
        network_calls.append(kw)
        return str(root)

    _install_fake_modelscope(monkeypatch, fake)
    monkeypatch.setattr(asr_mod, "Qwen3ONNXRunner", lambda model_dir: object())
    monkeypatch.setattr(asr_mod, "Qwen3Tokenizer", lambda tok_dir: object())
    monkeypatch.setattr(asr_mod.Qwen3ASRSTT, "_warmup", lambda self: None)

    stt = asr_mod.Qwen3ASRSTT(variant="0.6B")
    stt.load()

    assert len(network_calls) == 1
    assert "local_files_only" not in network_calls[0]
    assert "qwen3_snapshot_start" in recording_logger.events
    assert "qwen3_snapshot_done" in recording_logger.events
    assert "qwen3_cache_hit" not in recording_logger.events


def test_required_cache_files_cover_tokenizer_inputs(asr_mod):
    """清单必须覆盖 Qwen3Tokenizer 真要打开的三个文件(缺 merges.txt 会崩)。"""
    files = asr_mod._required_cache_files("0.6B")
    assert "tokenizer/vocab.json" in files
    assert "tokenizer/merges.txt" in files
    assert "tokenizer/tokenizer_config.json" in files
    assert "model_0.6B/decoder.int8.onnx" in files
