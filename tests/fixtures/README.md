# 测试 fixture 资源

## `zh.wav`(+ `zh.m4a` 源文件)

10.6 秒中文语音,内容为《出师表》开头:

> 先帝创业未半而中道崩殂,今天下三分,益州疲弊,此诚危急存亡之秋也。

`tests/test_sense_voice.py` 用它跑端到端 STT 推理冒烟测试。

**来源**:作者(项目作者 @pkulijing)自己用手机录的一条 m4a。原始 m4a 也一并放在这个目录(`zh.m4a`,~92 KB),作为可重新生成 wav 的"上游"。早期 PR 用过 FunASR 官方示例 `iic/SenseVoiceSmall/example/zh.mp3`,但作者觉得官方那条录音口音别扭,于是替换成自己的录音。

**为什么 commit 转换后的 wav 而不是只 commit m4a**:

- `tests/test_sense_voice.py` 走 `wave.open(wav_bytes)`,只认 WAV 容器
- m4a → wav 的转换需要 ffmpeg / afconvert 这种系统级工具,Linux CI 上缺,macOS 上也未必每个 dev 机都装
- wav 直接 commit 进 git 让测试零运行时依赖,341 KB 完全可以承受

**重新生成 wav 的方法**(macOS,系统自带 `afconvert`):

```bash
cd tests/fixtures
afconvert -f WAVE -d LEI16@16000 -c 1 zh.m4a zh.wav
```

参数解释:`-f WAVE` 容器格式 / `-d LEI16@16000` 16-bit 小端 PCM @ 16 kHz / `-c 1` 单声道。这正是 SenseVoice 训练时的输入规格,转出来的 wav 直接可以喂给 `SenseVoiceSTT.transcribe()`。

Linux 上等价命令(需要 `apt install ffmpeg`):

```bash
ffmpeg -i zh.m4a -ar 16000 -ac 1 -c:a pcm_s16le zh.wav
```

**许可**:作者自己录的内容(古文 + 自己的声音),与本项目代码同 MIT 许可。
