"""SenseVoice STT 端到端冒烟测试。

针对 src/whisper_input/stt/sense_voice.py 的 transcribe() 全链路:
WAV bytes → WavFrontend(fbank + LFR + CMVN) → ONNX 推理 → CTC 解码
→ SentencepiecesTokenizer → rich_transcription_postprocess → 文本

测试逻辑:
1. 读 tests/fixtures/zh.wav(10.6s 中文音频,作者自录的《出师表》开头,见 fixtures/README.md)
2. 实例化 SenseVoiceSTT,调 transcribe()。首次会通过 modelscope.snapshot_download
   拉取 ~231 MB 模型 + ~377 KB BPE tokenizer,落到 ~/.cache/modelscope/hub/。
   CI 上用 actions/cache 缓存这个目录,后续 run 直接命中
3. assert 输出含已知中文片段(完整内容: "先帝创业未半而中道崩殂,今天下三分,
   益州疲弊,此诚危急存亡之秋也。")

为什么不加 @pytest.mark.slow:
- 本地 dev 机型号大概率已经 cache 过模型(开发时跑过 whisper-input)
- CI 通过 cache 命中后只是几秒推理
- 项目核心路径理应在默认测试套里跑,有 slow marker 容易被忽略
"""

from pathlib import Path

import pytest

from whisper_input.stt.sense_voice import SenseVoiceSTT

FIXTURE = Path(__file__).parent / "fixtures" / "zh.wav"


@pytest.fixture(scope="module")
def stt() -> SenseVoiceSTT:
    """模块级 fixture: 整个 test_sense_voice.py 共享一个加载好的模型实例,
    避免每个用例都付一次 ONNX session 创建的成本。
    """
    instance = SenseVoiceSTT(language="zh", use_itn=True)
    instance.load()
    return instance


def test_fixture_exists():
    """sanity: fixture 文件存在,大小合理(10.6s @ 16kHz mono 16bit ≈ 340 KB)。"""
    assert FIXTURE.is_file()
    size = FIXTURE.stat().st_size
    assert 200_000 < size < 500_000, f"fixture size {size} 不在预期范围"


def test_transcribe_chinese_example(stt: SenseVoiceSTT):
    """跑完整推理链路,期望输出包含已知关键词。"""
    wav_bytes = FIXTURE.read_bytes()
    text = stt.transcribe(wav_bytes)

    # 内容是《出师表》开头:"先帝创业未半而中道崩殂,今天下三分,
    # 益州疲弊,此诚危急存亡之秋也。"
    # 不做精确字符串匹配 —— 量化模型在某些字上有可预期的小偏差(实测把
    # "未半"识成"未伴",把"诚危急"识成"称危及"),所以挑稳定识别的语义片段
    assert text  # 至少非空
    assert "先帝创业" in text, f"缺少'先帝创业': {text!r}"
    assert "天下三分" in text, f"缺少'天下三分': {text!r}"
    assert "益州" in text, f"缺少'益州': {text!r}"
    assert "存亡" in text, f"缺少'存亡': {text!r}"


def test_transcribe_empty_returns_empty(stt: SenseVoiceSTT):
    """空字节直接走 short-circuit,不该跑模型。"""
    assert stt.transcribe(b"") == ""


def test_transcribe_too_short_returns_empty(stt: SenseVoiceSTT):
    """< 0.1s(< 1600 samples)的音频被 short-circuit。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 800)  # 0.05s 静音
    assert stt.transcribe(buf.getvalue()) == ""
