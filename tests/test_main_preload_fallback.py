"""测试 WhisperInput.preload_model —— 37 轮重写。

36 轮的做法是**先问 DownloadManager「这个 variant 下过没有」再决定 load
谁**。那个前置判断要求我们自己算出 modelscope 的缓存落点,而 1.40 起
``snapshot_download`` 委托给 ``modelscope_hub``,落点由它内部
``find_reusable_legacy_repo_dir()`` 在四种历史布局间探测决定 —— 我们算不
准,实机上把明明在磁盘上的 0.6B 判成了"未下载"。

37 轮改成:**load() 自己就是权威**。命中缓存就秒回,没下就下载(慢的话有
``qwen3_download_slow`` 提示打到终端)。只在 load 失败时才回退 0.6B 重试。

行为变化(有意为之):配置 1.7B 而本地只有 0.6B 时,不再"静默改用 0.6B",
而是按用户配置去下 1.7B。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from daobidao.__main__ import WhisperInput


@pytest.fixture
def wi(monkeypatch):
    fake_stt = MagicMock()
    fake_stt.variant = "1.7B"  # 模拟用户配的是 1.7B
    fake_stt.cache_root = None
    monkeypatch.setattr(
        "daobidao.__main__.create_stt_engine",
        lambda cfg: fake_stt,
    )
    instance = WhisperInput(
        {
            "audio": {"sample_rate": 16000, "channels": 1},
            "sound": {"enabled": False},
            "tray_status": {"enabled": False},
            "overlay": {"enabled": False},
        }
    )
    yield instance
    instance.stop_worker(timeout=1.0)


def test_preload_loads_configured_variant_without_asking_cache(wi):
    """正常路径:直接 load 配置的 variant,不去问"下过没有"。"""
    original_stt = wi.stt

    with patch.object(
        wi.download_manager, "is_variant_downloaded"
    ) as never_called:
        ok = wi.preload_model()

    assert ok is True
    assert wi.stt is original_stt
    original_stt.load.assert_called_once()
    never_called.assert_not_called()


def test_preload_records_cache_root_after_success(wi):
    """load 成功后把 modelscope 给出的 cache_root 交给 DownloadManager。

    这是设置页"已下载 ✓"唯一可信的来源 —— 路径由 modelscope 自己算出,
    我们不再猜。
    """
    wi.stt.cache_root = "/tmp/fake-cache/snapshots/master"

    wi.preload_model()

    assert str(wi.download_manager.cache_root) == (
        "/tmp/fake-cache/snapshots/master"
    )


def test_preload_falls_back_to_0_6b_when_configured_load_fails(wi):
    """配置的 variant load 失败 → 回退 0.6B 再试一次。"""
    wi.stt.load.side_effect = RuntimeError("network down")

    fake_0_6b = MagicMock()
    fake_0_6b.variant = "0.6B"
    fake_0_6b.cache_root = "/tmp/fake-cache"

    with patch(
        "daobidao.stt.qwen3.Qwen3ASRSTT", return_value=fake_0_6b
    ) as mock_cls:
        ok = wi.preload_model()

    assert ok is True
    assert wi.stt is fake_0_6b
    mock_cls.assert_called_once_with(variant="0.6B")
    fake_0_6b.load.assert_called_once()


def test_preload_returns_false_when_fallback_also_fails(wi):
    """配置的和 0.6B 都 load 不起来 → 返 False,不换 stt。"""
    original_stt = wi.stt
    original_stt.load.side_effect = RuntimeError("network down")

    fake_0_6b = MagicMock()
    fake_0_6b.variant = "0.6B"
    fake_0_6b.load.side_effect = RuntimeError("network down")

    with patch("daobidao.stt.qwen3.Qwen3ASRSTT", return_value=fake_0_6b):
        ok = wi.preload_model()

    assert ok is False
    assert wi.stt is original_stt


def test_preload_no_double_attempt_when_configured_is_already_0_6b(
    wi, monkeypatch
):
    """配置本来就是 0.6B 且 load 失败 → 不再拿 0.6B 重试一遍。"""
    wi.stt.variant = "0.6B"
    wi.stt.load.side_effect = RuntimeError("network down")

    with patch("daobidao.stt.qwen3.Qwen3ASRSTT") as mock_cls:
        ok = wi.preload_model()

    assert ok is False
    mock_cls.assert_not_called()
