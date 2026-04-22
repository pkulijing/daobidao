# 测试 fixture 资源

## `zh.wav`(+ `zh.m4a` 源文件)

10.6 秒中文语音,内容为《出师表》开头:

> 先帝创业未半而中道崩殂,今天下三分,益州疲弊,此诚危急存亡之秋也。

`tests/test_qwen3_asr.py` 和 `tests/test_qwen3_runner.py` 用它跑端到端 STT 推理冒烟测试。

**来源**:作者(项目作者 @pkulijing)自己用手机录的一条 m4a。原始 m4a 也一并放在这个目录(`zh.m4a`,~92 KB),作为可重新生成 wav 的"上游"。早期 PR 用过 FunASR 官方示例 `iic/SenseVoiceSmall/example/zh.mp3`,但作者觉得官方那条录音口音别扭,于是替换成自己的录音。26 轮换到 Qwen3-ASR 之后同一条 wav 继续沿用,作为旧引擎(只能关键词匹配)到新引擎(原文逐字匹配)的回归基准。

**为什么 commit 转换后的 wav 而不是只 commit m4a**:

- 测试走 `wave.open(wav_bytes)` / `soundfile.read`,只认 WAV 容器
- m4a → wav 的转换需要 ffmpeg / afconvert 这种系统级工具,Linux CI 上缺,macOS 上也未必每个 dev 机都装
- wav 直接 commit 进 git 让测试零运行时依赖,341 KB 完全可以承受

**重新生成 wav 的方法**(macOS,系统自带 `afconvert`):

```bash
cd tests/fixtures
afconvert -f WAVE -d LEI16@16000 -c 1 zh.m4a zh.wav
```

参数解释:`-f WAVE` 容器格式 / `-d LEI16@16000` 16-bit 小端 PCM @ 16 kHz / `-c 1` 单声道。这也是 Qwen3-ASR / Whisper log-mel extractor 的标准输入规格,转出来的 wav 直接可以喂给 `Qwen3ASRSTT.transcribe()`。

Linux 上等价命令(需要 `apt install ffmpeg`):

```bash
ffmpeg -i zh.m4a -ar 16000 -ac 1 -c:a pcm_s16le zh.wav
```

**许可**:作者自己录的内容(古文 + 自己的声音),与本项目代码同 MIT 许可。

## `whisper_mel_golden_zh.npy`

`zh.wav` 的 Whisper 官方 log-mel 结果,形状 `(128, 3000)` float32。由 `scripts/generate_whisper_mel_golden.py`(用 `transformers.WhisperFeatureExtractor`)生成并 commit 进 repo,`tests/test_qwen3_feature.py` 断言我们自己实现的 `log_mel_spectrogram()` 跟它 `np.allclose(rtol=1e-4)`。

这是迁移 Qwen3-ASR 时锚定"我们的特征提取和上游参考实现位对齐"的硬约束:将来 bump 任何特征提取相关的代码,golden 不动,测试就会立刻捕获漂移。
