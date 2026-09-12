"""Qwen3-ASR STT backend (offline + streaming).

Press-and-hold 离线路径(26 轮):

    load(): snapshot_download → build Qwen3ONNXRunner + Qwen3Tokenizer →
            run a one-off warmup so the first real transcription doesn't
            include graph-init overhead.

    transcribe(wav_bytes):
        1. decode wav → float32 mono 16 kHz
        2. pad/trim to 30s, log-mel spectrogram
        3. encode_audio → audio_features
        4. build chat-template prompt with N audio_pads (N = audio-token len)
        5. decoder prefill + greedy generation until <|im_end|>
        6. decode + postprocess → final transcript

流式路径(28 轮,策略 E):详见 ``_stream.py``。本类暴露
``init_stream_state()`` / ``stream_step()``;离线 ``transcribe()`` 保留,
用户在设置页关流式时走它。
"""

from __future__ import annotations

import contextlib
import io
import logging
import threading
import time
import wave
from pathlib import Path
from typing import ClassVar, Literal

import numpy as np

from daobidao.i18n import t
from daobidao.logger import get_logger
from daobidao.stt.base import BaseSTT, StreamEvent
from daobidao.stt.qwen3._feature import (
    SAMPLE_RATE,
    log_mel_spectrogram,
    pad_or_trim,
)
from daobidao.stt.qwen3._onnx_runner import Qwen3ONNXRunner
from daobidao.stt.qwen3._postprocess import parse_asr_output
from daobidao.stt.qwen3._prompt import build_prompt
from daobidao.stt.qwen3._stream import (
    Qwen3StreamState,
)
from daobidao.stt.qwen3._stream import (
    init_stream_state as _init_stream_state,
)
from daobidao.stt.qwen3._stream import (
    stream_step as _stream_step,
)
from daobidao.stt.qwen3._tokenizer import Qwen3Tokenizer

logger = get_logger(__name__)

REPO_ID = "zengshuishui/Qwen3-ASR-onnx"
Variant = Literal["0.6B", "1.7B"]
VALID_VARIANTS: tuple[Variant, ...] = ("0.6B", "1.7B")

# 37 轮:snapshot_download 超过这个秒数还没返回,就在终端提一句"在下模型,
# 别急"。modelscope 只打一个按文件计数的聚合进度条(9 个文件),最大那个
# decoder.int8.onnx(756 MB)会让它在同一格上停数分钟不动,看起来像卡死。
# 缓存命中时这个调用 ~1s 返回,计时器直接被 cancel。
_SLOW_DOWNLOAD_HINT_S = 3.0

# 只用于上面那句提示的文案,不参与任何下载逻辑。
_VARIANT_DOWNLOAD_SIZE: dict[str, str] = {
    "0.6B": "990 MB",
    "1.7B": "2.4 GB",
}

# Upper bound on tokens generated per utterance. Based on empirical check: a
# 10s Chinese sample emits ~30 tokens; 60s ≤ ~250. 400 gives plenty of slack
# without risking runaway generation.
_MAX_NEW_TOKENS = 400

# Minimum audio duration (0.1s). Below this we skip inference — the user
# probably tapped the hotkey by accident.
_MIN_SAMPLES = int(SAMPLE_RATE * 0.1)


def _required_cache_files(variant: str) -> tuple[str, ...]:
    """缓存算不算「命中」要看到的文件（相对 cache root）。

    模型侧是这个 variant 的 3 个 ONNX；tokenizer 侧是 ``Qwen3Tokenizer``
    真正会打开的三个文件（它没有 ``tokenizer.json``，是拿
    ``vocab.json`` + ``merges.txt`` 现搭 byte-level BPE，再加
    ``tokenizer_config.json`` 里的 added tokens）。少一个都不算命中 ——
    否则我们要么在构造 runner 时才炸，要么在构造 tokenizer 时才炸。
    """
    return (
        f"model_{variant}/conv_frontend.onnx",
        f"model_{variant}/encoder.int8.onnx",
        f"model_{variant}/decoder.int8.onnx",
        "tokenizer/tokenizer_config.json",
        "tokenizer/vocab.json",
        "tokenizer/merges.txt",
    )


@contextlib.contextmanager
def _mute_benign_revision_warning():
    """临时压掉 modelscope_hub 那条「无法确认 revision」的 WARNING。

    ``local_files_only=True`` 命中缓存时 modelscope_hub 必打一条
    ``Cannot confirm the cached file is for revision: master``：它想说的是
    「本地没存 revision 信息，我没法替你确认」，而我们要的信息（文件在不在）
    紧接着自己复核了。这条对用户没有任何可操作性，却是 WARNING —— 会经控制台
    通道打到终端，变成每次启动吓人一次。只在探测期间压掉，别影响真下载。
    """
    target = logging.getLogger("modelscope_hub.download")
    previous = target.level
    target.setLevel(logging.ERROR)
    try:
        yield
    finally:
        target.setLevel(previous)


class Qwen3ASRSTT(BaseSTT):
    """Qwen3-ASR int8 ONNX inference, 支持离线 + 流式(策略 E)。"""

    supports_streaming: ClassVar[bool] = True

    def __init__(self, variant: str = "0.6B"):
        if variant not in VALID_VARIANTS:
            raise ValueError(
                f"unknown variant {variant!r}; expected one of {VALID_VARIANTS}"
            )
        self.variant: Variant = variant  # type: ignore[assignment]
        self.cache_root: Path | None = None
        self._runner: Qwen3ONNXRunner | None = None
        self._tokenizer: Qwen3Tokenizer | None = None

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _start_slow_download_hint(self) -> threading.Timer:
        """起一个计时器,下载久了就在终端提一句。调用方负责 cancel。"""

        def _hint() -> None:
            logger.info(
                "qwen3_download_slow",
                variant=self.variant,
                message=t(
                    "stt.download_slow",
                    variant=self.variant,
                    size=_VARIANT_DOWNLOAD_SIZE.get(self.variant, ""),
                ),
            )

        timer = threading.Timer(_SLOW_DOWNLOAD_HINT_S, _hint)
        timer.daemon = True
        timer.start()
        return timer

    def load(self) -> None:
        if self._runner is not None and self._tokenizer is not None:
            return

        logger.info("qwen3_asr_loading", variant=self.variant)

        from modelscope import snapshot_download

        t0 = time.perf_counter()
        allow_patterns = [
            f"model_{self.variant}/conv_frontend.onnx",
            f"model_{self.variant}/encoder.int8.onnx",
            f"model_{self.variant}/decoder.int8.onnx",
            "tokenizer/*",
        ]

        local_root = self._probe_local_cache(allow_patterns)
        if local_root is not None:
            # 缓存已经够了:一次网络请求都不发。断网 / 代理配错的机器靠这条
            # 才能启动(否则连"缓存里有没有"都要先联网问一次)。
            self.cache_root = local_root
            logger.info(
                "qwen3_cache_hit",
                variant=self.variant,
                path=str(local_root),
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        else:
            logger.info("qwen3_snapshot_start", variant=self.variant)
            # modelscope snapshot_download 用 print() 打 "Downloading Model from
            # ... to directory: ..." 一行(每次启动 cache 命中也照打),不走 stdlib
            # logging,我们 logger.info 那条 qwen3_snapshot_start 已经覆盖同样信息,
            # 这里把它的 stdout 吞了避免污染 terminal。出错时把吞下的内容转 log
            # 留诊断;tqdm 进度条走 stderr 不受影响,真下载时仍可见。
            captured = io.StringIO()
            hint = self._start_slow_download_hint()
            try:
                with contextlib.redirect_stdout(captured):
                    self.cache_root = Path(
                        snapshot_download(
                            REPO_ID, allow_patterns=allow_patterns
                        )
                    )
            except Exception:
                if captured.getvalue().strip():
                    logger.error(
                        "modelscope_stdout_at_error",
                        output=captured.getvalue(),
                    )
                raise
            finally:
                hint.cancel()
            logger.info(
                "qwen3_snapshot_done",
                variant=self.variant,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )

        # round 33 诊断:打 ONNX 文件 size,排查 cache 损坏。
        self._log_onnx_file_sizes()

        t0 = time.perf_counter()
        logger.info("qwen3_runner_start")
        self._runner = Qwen3ONNXRunner(
            self.cache_root / f"model_{self.variant}"
        )
        logger.info(
            "qwen3_runner_ready",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )

        t0 = time.perf_counter()
        self._tokenizer = Qwen3Tokenizer(self.cache_root / "tokenizer")
        logger.info(
            "qwen3_tokenizer_ready",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )

        t0 = time.perf_counter()
        logger.info("qwen3_warmup_start")
        self._warmup()
        logger.info(
            "qwen3_warmup_done",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )

        logger.info("qwen3_asr_loaded", variant=self.variant)

    def _probe_local_cache(self, allow_patterns: list[str]) -> Path | None:
        """只查本地、不发网络请求地问 modelscope:缓存够不够用?

        ``local_files_only=True`` 让 modelscope 只读本地索引,命中时返回**它
        自己认定的** cache root —— 这正是 37 轮定下的规矩:缓存落点由
        modelscope 说了算,我们不自己拼路径(自算路径在 modelscope 1.40 上曾
        把磁盘上真有的模型判成"未下载")。

        但它的"命中"标准只到「目录非空」为止(modelscope_hub 的
        local_files_only 分支只做 ``any(output_dir.iterdir())``),所以这里
        还要按本 variant 真正会用到的文件复核一遍:否则「只有 tokenizer」或
        「只有另一个 variant」的目录也会被当成缓存,一路走到构造 runner /
        tokenizer 时才炸。

        任何异常(旧版 modelscope 不认这个参数、本地确实没有、索引损坏)都返回
        ``None``,由调用方走原来的联网路径。这条路**只做加速,不承担正确性**:
        它在任何时候失败都等价于改动前的行为。

        收益:模型已缓存时启动零网络请求 —— 断网、代理配错(见 issue #17 的
        邻居 bug)都不再让启动卡住或直接崩。
        """
        from modelscope import snapshot_download

        with _mute_benign_revision_warning():
            try:
                root = Path(
                    snapshot_download(
                        REPO_ID,
                        allow_patterns=allow_patterns,
                        local_files_only=True,
                    )
                )
            except Exception as exc:
                logger.debug("qwen3_local_probe_miss", error=repr(exc))
                return None

        missing = [
            rel
            for rel in _required_cache_files(self.variant)
            if not (root / rel).exists()
        ]
        if missing:
            logger.debug(
                "qwen3_local_probe_incomplete",
                path=str(root),
                missing=missing,
            )
            return None
        return root

    def _log_onnx_file_sizes(self) -> None:
        """打 ONNX 文件 size,round 33 加的诊断,定位 cache 损坏假设。"""
        assert self.cache_root is not None
        model_dir = self.cache_root / f"model_{self.variant}"
        sizes = {}
        for name in (
            "conv_frontend.onnx",
            "encoder.int8.onnx",
            "decoder.int8.onnx",
        ):
            p = model_dir / name
            sizes[name] = p.stat().st_size if p.exists() else None
        logger.info(
            "qwen3_onnx_file_sizes",
            variant=self.variant,
            sizes=sizes,
        )

    def _warmup(self) -> None:
        """跑一遍 prefill + 几步 greedy,检查输出非平凡。

        round 33 起改用 fixed-seed Gaussian noise(不再是 silence)+ 三条
        assert: logits finite / 非全 0 / greedy 至少吐 1 个非 EOS token。
        warmup 失败抛 RuntimeError,把 silent garbage 在 load 阶段就暴露
        出来,而不是等 transcribe 返空。
        """
        assert self._runner is not None
        assert self._tokenizer is not None

        # 1s 高斯噪声(峰值 ~0.05),非零 finite 信号,比静音更接近真实 workload。
        # 固定 seed 保证 warmup 输出可复现,便于诊断。
        rng = np.random.default_rng(0)
        audio = rng.standard_normal(SAMPLE_RATE).astype(np.float32) * 0.05
        padded = pad_or_trim(audio)
        mel = log_mel_spectrogram(padded)
        audio_features = self._runner.encode_audio(mel)

        prompt = build_prompt(audio_features.shape[1])
        prompt_ids = self._tokenizer.encode(prompt)
        input_ids = np.array([prompt_ids], dtype=np.int64)
        caches = self._runner.alloc_decoder_caches()

        logits = self._runner.decoder_step(
            input_ids, audio_features, caches, cur_len=0
        )

        prefill_stats = _logits_stats(logits)
        logger.info(
            "qwen3_warmup_logits_stats",
            variant=self.variant,
            **prefill_stats,
        )

        if not prefill_stats["all_finite"]:
            raise RuntimeError(
                f"qwen3 warmup produced degenerate output (variant="
                f"{self.variant}): logits 非 finite,stats={prefill_stats}"
            )
        if not prefill_stats["any_nonzero"]:
            raise RuntimeError(
                f"qwen3 warmup produced degenerate output (variant="
                f"{self.variant}): logits 全 0,stats={prefill_stats}"
            )

        # 跑 5 步 greedy,收集 generated。如果模型坏到第 1 步就选 EOS,
        # generated 为空 —— 这是 transcribe 返空的典型根因。
        eos_id = self._tokenizer.eos_id
        cur_len = len(prompt_ids)
        generated: list[int] = []
        for _ in range(5):
            next_id = int(np.argmax(logits[0, -1]))
            if next_id == eos_id:
                break
            generated.append(next_id)
            next_input = np.array([[next_id]], dtype=np.int64)
            logits = self._runner.decoder_step(
                next_input, audio_features, caches, cur_len
            )
            cur_len += 1

        logger.info(
            "qwen3_warmup_greedy",
            variant=self.variant,
            generated_count=len(generated),
            generated_ids=generated[:5],
        )

        if not generated:
            raise RuntimeError(
                f"qwen3 warmup produced degenerate output (variant="
                f"{self.variant}): greedy decode 第 1 步就选 EOS,"
                f"prefill_stats={prefill_stats}"
            )

    # ------------------------------------------------------------------
    # Transcribe
    # ------------------------------------------------------------------

    def transcribe(self, wav_data: bytes) -> str:
        if not wav_data:
            return ""
        self.load()
        assert self._runner is not None and self._tokenizer is not None

        audio = _wav_bytes_to_float32(wav_data)
        if len(audio) < _MIN_SAMPLES:
            return ""

        padded = pad_or_trim(audio)
        mel = log_mel_spectrogram(padded)
        audio_features = self._runner.encode_audio(mel)

        prompt = build_prompt(audio_features.shape[1])
        prompt_ids = self._tokenizer.encode(prompt)
        input_ids = np.array([prompt_ids], dtype=np.int64)

        caches = self._runner.alloc_decoder_caches()
        logits = self._runner.decoder_step(
            input_ids, audio_features, caches, cur_len=0
        )
        cur_len = len(prompt_ids)

        # round 33 诊断:打 prefill 后 logits 统计,定位"transcribe 返空"。
        logger.info(
            "qwen3_transcribe_prefill_done",
            variant=self.variant,
            prompt_len=len(prompt_ids),
            audio_features_shape=list(audio_features.shape),
            **_logits_stats(logits),
        )

        eos_id = self._tokenizer.eos_id
        generated: list[int] = []
        hit_eos = False
        for _ in range(_MAX_NEW_TOKENS):
            next_id = int(np.argmax(logits[0, -1]))
            if next_id == eos_id:
                hit_eos = True
                break
            generated.append(next_id)
            next_input = np.array([[next_id]], dtype=np.int64)
            logits = self._runner.decoder_step(
                next_input, audio_features, caches, cur_len
            )
            cur_len += 1

        logger.info(
            "qwen3_transcribe_decode_done",
            variant=self.variant,
            generated_count=len(generated),
            first_5_token_ids=generated[:5],
            hit_eos=hit_eos,
            hit_max=not hit_eos and len(generated) == _MAX_NEW_TOKENS,
        )

        raw = self._tokenizer.decode(generated, skip_special_tokens=True)
        return parse_asr_output(raw)

    # ------------------------------------------------------------------
    # Streaming(策略 E,详见 _stream.py)
    # ------------------------------------------------------------------

    def init_stream_state(self) -> Qwen3StreamState:
        """为一次按键→说话→松手周期初始化状态。"""
        self.load()
        assert self._runner is not None and self._tokenizer is not None
        return _init_stream_state(self._runner, self._tokenizer)

    def stream_step(
        self,
        audio_chunk: np.ndarray,
        state: Qwen3StreamState,
        is_last: bool,
    ) -> StreamEvent:
        """增量喂一段音频 chunk;具体算法见 ``_stream.py``。

        ``audio_chunk`` 应该是 float32 1D array,16 kHz 单声道。空数组合法
        (用于纯 flush 场景)。
        """
        assert self._runner is not None and self._tokenizer is not None
        return _stream_step(
            state,
            audio_chunk,
            is_last,
            runner=self._runner,
            tokenizer=self._tokenizer,
        )


# --------------------------------------------------------------------------
# Diagnostic helpers (round 33)
# --------------------------------------------------------------------------


def _logits_stats(logits: np.ndarray) -> dict:
    """Logits 统计,塞进 structlog event。

    `all_finite` / `any_nonzero` 是 warmup assert 直接用的两条;
    min/max/mean 给诊断用,float() 转 Python 标量便于 JSON 序列化。
    """
    finite = np.isfinite(logits)
    return {
        "all_finite": bool(finite.all()),
        "any_nonzero": bool((logits != 0).any()),
        "shape": list(logits.shape),
        "min": float(logits[finite].min()) if finite.any() else None,
        "max": float(logits[finite].max()) if finite.any() else None,
        "mean": float(logits[finite].mean()) if finite.any() else None,
    }


# --------------------------------------------------------------------------
# WAV byte decoding
# --------------------------------------------------------------------------


def _wav_bytes_to_float32(wav_data: bytes) -> np.ndarray:
    """Decode a 16 kHz 16-bit mono WAV blob to float32 [-1, 1] 1D array."""
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
