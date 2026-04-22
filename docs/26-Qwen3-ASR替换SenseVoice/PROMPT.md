# 需求:Qwen3-ASR 替换 SenseVoice(离线模式)

## 背景

当前 STT 引擎是 SenseVoice-Small(iic 官方 int8 ONNX)。这个模型本身已经
很好(RTF ~0.1x,中英日韩粤多语种),但在**中英混输 + 专业技术术语**这
个我们核心用户画像的场景下识别质量有明显短板:`kubernetes` / `tkinter`
/ `TypeScript` / `onnxruntime` 这种词经常翻车。

第 1 轮的 SUMMARY 讨论过换模型的方向,当时的结论是"等 Qwen3-ASR + vLLM"
——但那个判断是 Qwen3-ASR 刚开源时(2026-01-30)的状态。2026-04 复盘时
发现生态已经成熟:

- ModelScope 仓库 `zengshuishui/Qwen3-ASR-onnx` 提供 0.6B 和 1.7B 的
  int8 ONNX 变体
- GitHub 仓库 `Wasser1462/Qwen3-ASR-onnx` 提供完整的 Python +
  onnxruntime 推理参考代码
- **不需要 vLLM、不需要 torch、不需要任何 PyTorch 生态依赖**即可在 CPU 上
  跑 Qwen3-ASR

第 25 轮完成的 spike(见 `scripts/spike_qwen3_onnx.py` 和
`BACKGROUND.md`)验证了三段 ONNX 的接口契约、KV cache 语义、特征提取
维度等关键技术前提,确认本路线可行。

## 本轮目标

### 核心目标

1. **彻底替换 SenseVoice**:删除 `stt/sense_voice.py` / `_wav_frontend.py`
   / `_tokenizer.py` / `_postprocess.py`,去掉 `kaldi-native-fbank` 和
   `sentencepiece` 依赖
2. **Qwen3-ASR 离线推理落地**:实现 `Qwen3ASRSTT.transcribe(wav_bytes)
   → str`,行为跟当前 SenseVoice 一致(按住热键 → 松开 → 一次 paste)
3. **0.6B 和 1.7B 两款模型共用同一套代码**,只差权重路径。默认 0.6B
   (下载 ~990MB / 运行 ~1.5GB RAM),1.7B 作为 opt-in 选项(下载 ~2.4GB
   / 运行 ~3GB RAM)
4. **设置页提供模型切换下拉**,选中后热切换(不需要重启应用)
5. **测试覆盖率显著提升**。纯 Python 模块目标 95%+,整体从当前 51% 拉到
   70%+

### 非目标(本轮明确不做)

- **流式识别**:整条挪到第 27 轮独立攻克。本轮**不实现**
  `transcribe_streaming()`,接口**不扩展**(保持 `BaseSTT` 现状),避免过
  早设计。但实现时要求代码结构对未来流式友好——conv_frontend / encoder
  / decoder 三段 session 分开,不要写死"一次推理"的约束
- **hotwords / 关键词增强**:Qwen3-ASR 原生支持 prompt biasing,留第 28
  轮
- **自适应纠错**:第 29 轮及以后
- **保留 SenseVoice 作为轻量选项**:不做。SenseVoice 整体删除

## 硬约束

1. **完全本地运行**,不向云端发送音频或文字
2. **不引入 PyTorch**:任何带 `torch` 依赖的方案(`funasr` 全量、
   `transformers.AutoModel`、vLLM)一律排除
3. **不引入 scipy**:Wasser1462 参考代码里的 `scipy.signal.resample_poly`
   和 `scipy.io.wavfile.read` 用 numpy 手写或 soundfile 替代
4. **不引入 transformers 全量包**:Wasser1462 代码里 `AutoProcessor` /
   `AutoConfig` / `WhisperFeatureExtractor` 要替换成轻量实现
5. **强依赖 ModelScope 作为唯一模型分发源**:不设计 HuggingFace
   fallback,代码里不留 fallback 接口,不写"TODO: HF fallback"占位
6. **Whisper log-mel 特征提取必须自己实现**:数值必须跟 Whisper 官方
   `WhisperFeatureExtractor` 对齐(`np.allclose(rtol=1e-4)`)

## 验收标准

### 功能正确性

- [ ] 按住热键说一句话,松手后屏幕上出现识别文本,质量与 Wasser1462 原版
      推理结果一致
- [ ] 中英混输("今天要部署 kubernetes 集群"、"TypeScript 代码要重
      构")识别质量显著优于原 SenseVoice,定性验证通过
- [ ] 设置页"识别模型"下拉可选 `0.6B (快)` / `1.7B (更准)`,切换后
      在后台加载新模型,加载完成前显示"切换中"提示,加载完成后立即可用
- [ ] 切换 0.6B ↔ 1.7B 过程中旧 session 正确释放(RSS 不持续累积)
- [ ] 首次下载走 ModelScope,0.6B 中国带宽下 < 3 分钟,1.7B < 6 分钟
- [ ] 冷启动(第二次起)模型加载 < 5 秒(0.6B)

### 代码质量

- [ ] 删除 SenseVoice 全部代码
- [ ] 删除 `kaldi-native-fbank` / `sentencepiece` 依赖
- [ ] 新增 `tokenizers` 依赖(HF 的 Rust tokenizer 库,~10MB)
- [ ] **单元测试覆盖率**:
  - `stt/qwen3/_feature.py`(log-mel 特征提取)≥ 95%
  - `stt/qwen3/_tokenizer.py`(tokenizer 包装)≥ 95%
  - `stt/qwen3/_prompt.py`(prompt 构建)≥ 95%
  - `stt/qwen3/_onnx_runner.py`(ONNX 推理)≥ 85%
  - `stt/qwen3/qwen3_asr.py`(主类)≥ 85%
  - **整体覆盖率从当前 51% 提升到 ≥ 70%**
- [ ] log-mel 有 golden npy 文件对齐测试(跟 `transformers`
      `WhisperFeatureExtractor` 输出 `np.allclose(rtol=1e-4)`)
- [ ] 端到端 smoke test:`tests/fixtures/zh.wav` 跑 0.6B 推理,输出稳定
- [ ] CLAUDE.md 重写:Project Overview / Architecture / Key Technical
      Decisions / Dependencies 四节全部替换成 Qwen3-ASR
- [ ] BACKLOG.md 更新:旧的"中英混杂 / 专业词汇的识别后处理"条目删除
      或更新(挪到第 28 轮)

### 交付物

- `src/whisper_input/stt/qwen3/`(新)
- `tests/test_qwen3_*.py`(新)
- `tests/fixtures/whisper_mel_golden_zh.npy`(新,log-mel 对齐基准)
- SenseVoice 相关文件全部删除
- `CLAUDE.md` 更新
- `BACKLOG.md` 更新
- 本轮 `SUMMARY.md`

## 局限性(本轮承认不解决)

1. **Wasser1462 license 未明确**。本轮把他的 `qwen3_asr.py` 当作参考代码
   使用,发布前需要向作者索取 license 声明,或基于 QwenLM 官方代码
   (Apache-2.0)重写
2. **流式识别**:这一轮完全不做,留 27 轮
3. **1.7B 在 8GB RAM 机器上可能卡**,不做机器规格探测,用户自担
4. **没有 HuggingFace fallback**:ModelScope 崩了应用下载失败

## 后续 TODO(同步 BACKLOG.md)

- **第 27 轮:真流式识别** —— 本轮奠定的推理基础设施 + chunked encoder
  + rollback decoder 状态机
- **第 28 轮:hotwords / prompt biasing** —— Qwen3-ASR 原生支持
- **第 29 轮及以后:自适应纠错系统** —— 观察用户修正 → 自动维护 hotword
