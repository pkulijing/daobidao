"""Tests for ``whisper_input.stt.qwen3._onnx_runner.Qwen3ONNXRunner``.

Uses the real 0.6B model from the ``qwen3_0_6b_model_dir`` session fixture;
skips when the cache is absent.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from whisper_input.stt.qwen3._feature import (
    N_MELS,
    log_mel_spectrogram,
    pad_or_trim,
)
from whisper_input.stt.qwen3._onnx_runner import Qwen3ONNXRunner


@pytest.fixture(scope="module")
def runner(qwen3_0_6b_model_dir: Path) -> Qwen3ONNXRunner:
    return Qwen3ONNXRunner(qwen3_0_6b_model_dir)


# --------------------------------------------------------------------------
# Introspection
# --------------------------------------------------------------------------

def test_decoder_layer_count_is_28(runner: Qwen3ONNXRunner):
    assert runner.num_layers == 28


def test_decoder_kv_dims_match_spike(runner: Qwen3ONNXRunner):
    assert runner.kv_heads == 8
    assert runner.head_dim == 128


def test_output_names_ordering(runner: Qwen3ONNXRunner):
    names = runner._decoder_output_names
    assert names[0] == "logits"
    assert len(names) == 1 + 2 * runner.num_layers
    for i in range(runner.num_layers):
        assert names[1 + 2 * i] == f"key_delta_{i}"
        assert names[1 + 2 * i + 1] == f"value_delta_{i}"


def test_inspect_decoder_raises_when_no_cache_inputs(
    runner: Qwen3ONNXRunner,
):
    """Defensive path: if a future ONNX export drops ``cache_key_*`` we
    surface a clear RuntimeError instead of silently inferring layers=0."""
    # Swap in a fake decoder session whose get_inputs() returns nothing
    fake_decoder = MagicMock()
    fake_decoder.get_inputs.return_value = []
    original_decoder = runner.decoder
    runner.decoder = fake_decoder
    try:
        with pytest.raises(RuntimeError, match="cache_key_"):
            runner._inspect_decoder()
    finally:
        runner.decoder = original_decoder


# --------------------------------------------------------------------------
# Audio encoding
# --------------------------------------------------------------------------

def test_encode_audio_shape(runner: Qwen3ONNXRunner):
    audio = np.zeros(16000 * 30, dtype=np.float32)
    mel = log_mel_spectrogram(audio)
    assert mel.shape == (N_MELS, 3000)

    audio_features = runner.encode_audio(mel)
    assert audio_features.ndim == 3
    assert audio_features.shape[0] == 1
    assert audio_features.shape[2] == 1024
    # 30s → hundreds of audio tokens (exact count is graph-internal but stable)
    assert 100 < audio_features.shape[1] < 1500


def test_encode_audio_rejects_wrong_rank(runner: Qwen3ONNXRunner):
    with pytest.raises(ValueError, match="N_MELS"):
        runner.encode_audio(np.zeros((3000,), dtype=np.float32))


def test_encode_audio_coerces_non_float32(runner: Qwen3ONNXRunner):
    audio = np.zeros(16000 * 5, dtype=np.float32)
    mel64 = log_mel_spectrogram(audio).astype(np.float64)
    # Should not raise — runner coerces dtype
    out = runner.encode_audio(mel64)
    assert out.dtype == np.float32


# --------------------------------------------------------------------------
# KV cache allocation
# --------------------------------------------------------------------------

def test_alloc_decoder_caches_count_and_shape(runner: Qwen3ONNXRunner):
    caches = runner.alloc_decoder_caches()
    assert len(caches) == 2 * runner.num_layers
    expected_shape = (
        1,
        runner.max_total_len,
        runner.kv_heads,
        runner.head_dim,
    )
    for c in caches:
        assert c.shape == expected_shape
        assert c.dtype == np.float32
        assert (c == 0).all()


# --------------------------------------------------------------------------
# Decoder step — prefill + single-step generation
# --------------------------------------------------------------------------

def test_decoder_step_prefill_shape(runner: Qwen3ONNXRunner):
    # Fake audio_features: (1, 100, 1024)
    audio_features = np.zeros((1, 100, 1024), dtype=np.float32)
    # Short prompt
    input_ids = np.zeros((1, 10), dtype=np.int64)
    caches = runner.alloc_decoder_caches()

    logits = runner.decoder_step(
        input_ids, audio_features, caches, cur_len=0
    )
    # vocab size matches Qwen3 tokenizer (spike confirmed: 151936)
    assert logits.shape == (1, 10, 151936)


def test_decoder_step_writes_cache(runner: Qwen3ONNXRunner):
    audio_features = np.random.RandomState(0).randn(1, 50, 1024).astype(
        np.float32
    )
    input_ids = np.array([[1, 2, 3, 4, 5]], dtype=np.int64)
    caches = runner.alloc_decoder_caches()

    # Before: all zeros
    assert (caches[0] == 0).all()
    runner.decoder_step(input_ids, audio_features, caches, cur_len=0)

    # After: first 5 positions of first-layer key cache are populated
    written = caches[0][0, :5]
    assert not (written == 0).all(), "expected KV cache to be updated"
    # Positions after `seq` should remain zero
    unwritten = caches[0][0, 5:]
    assert (unwritten == 0).all()


def test_decoder_step_second_call_extends_cache(runner: Qwen3ONNXRunner):
    audio_features = np.random.RandomState(1).randn(1, 50, 1024).astype(
        np.float32
    )
    caches = runner.alloc_decoder_caches()

    # Step 1: prefill 5 tokens
    prefill_ids = np.array([[10, 20, 30, 40, 50]], dtype=np.int64)
    runner.decoder_step(prefill_ids, audio_features, caches, cur_len=0)
    snapshot_first5 = caches[0][0, :5].copy()

    # Step 2: one new token at position 5
    new_ids = np.array([[60]], dtype=np.int64)
    runner.decoder_step(new_ids, audio_features, caches, cur_len=5)

    # First 5 positions MUST be preserved
    assert np.array_equal(caches[0][0, :5], snapshot_first5)
    # Position 5 is now populated
    assert not (caches[0][0, 5] == 0).all()
    # Position 6+ still zero
    assert (caches[0][0, 6:] == 0).all()


def test_decoder_step_cache_overflow_raises(runner: Qwen3ONNXRunner):
    audio_features = np.zeros((1, 50, 1024), dtype=np.float32)
    # cur_len very close to max_total_len → next step overflows
    caches = runner.alloc_decoder_caches()
    input_ids = np.zeros((1, 5), dtype=np.int64)

    with pytest.raises(RuntimeError, match="overflow"):
        runner.decoder_step(
            input_ids,
            audio_features,
            caches,
            cur_len=runner.max_total_len - 2,
        )


# --------------------------------------------------------------------------
# Integration: encode → prefill produces non-trivial logits
# --------------------------------------------------------------------------

def test_real_audio_prefill_produces_plausible_logits(
    runner: Qwen3ONNXRunner,
):
    import soundfile as sf

    wav = Path(__file__).parent / "fixtures" / "zh.wav"
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    assert sr == 16000

    padded = pad_or_trim(audio)
    mel = log_mel_spectrogram(padded)
    audio_features = runner.encode_audio(mel)

    # Minimal prompt: a single BOS-like token works for shape sanity
    input_ids = np.array([[151644]], dtype=np.int64)  # <|im_start|>
    caches = runner.alloc_decoder_caches()
    logits = runner.decoder_step(
        input_ids, audio_features, caches, cur_len=0
    )
    assert logits.shape == (1, 1, 151936)
    # Must not be all NaN / all zero
    assert np.isfinite(logits).all()
    assert (logits != 0).any()
