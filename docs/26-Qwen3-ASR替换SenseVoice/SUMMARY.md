# 26 轮 - Qwen3-ASR 替换 SenseVoice - 总结

> **⚠ 本轮成果不直接合入 master,走独立分支 `feat/qwen3-stream` 暂存**。
> 原因:识别质量跃迁已经验证(出师表原文零错字),但使用体验上还有两个
> 明显遗留问题(**冷启动慢、松手后 ~2s 延迟、1.7B 点选即触发 2.4 GB 无
> 反馈下载**,详见"局限性"第 7-9 条),属于"模型换好了但产品化还差一口气"
> 的半成品状态。分支名 `feat/qwen3-stream` 取"Qwen3 + streaming"之意 ——
> 这条分支预期在第 27 轮流式识别做完之后再一起合入 master,因为流式
> 本身会稀释掉冷启动慢和松手延迟两个痛点,到时候用户体验才真正完整。

## 开发项背景

- 自 12 轮起 STT 一直用达摩院官方 `iic/SenseVoiceSmall-onnx`(int8),延续
  到 25 轮。SenseVoice 在**中英混输 + 技术术语**这个我们核心用户画像的
  场景下有明显短板:`kubernetes` / `tkinter` / `TypeScript` /
  `onnxruntime` 这类词经常翻车,单元测试都不得不写成"关键词匹配"而非
  原文对齐(见 15 轮 `tests/test_sense_voice.py`)
- 2026 年 4 月复盘时生态已经成熟:ModelScope 仓库
  `zengshuishui/Qwen3-ASR-onnx` 提供了 Qwen3-ASR 0.6B / 1.7B 两个尺寸的
  int8 ONNX 导出,GitHub `Wasser1462/Qwen3-ASR-onnx` 提供了基于
  `onnxruntime` 的纯 Python 推理参考实现 ——**不需要 vLLM,不需要
  torch,不需要任何 PyTorch 生态依赖**就能在 CPU 上跑 Qwen3-ASR
- 25 轮末尾(严格地说:第 26 轮开发周期内的 spike 前置步骤)跑了
  `scripts/spike_qwen3_onnx.py` 验证了 3 段 ONNX 的接口契约、28 层 KV
  cache 结构、`cache_position` 绝对寻址语义,确认方案可行

## 实现方案

### 关键设计

**核心价值:识别质量从"关键词对"跃迁到"原文逐字对"**。同一条
`tests/fixtures/zh.wav`(10.6 秒《出师表》开头),SenseVoice 只能模糊识别
出片段,Qwen3-ASR 0.6B 直接给出:

> 先帝创业未半而中道崩殂,今天下三分,益州疲弊,此诚危急存亡之秋也。

零错字。这让 `tests/test_qwen3_asr.py` 可以直接 `assert expected in result`
而不是像 SenseVoice 时代那样写一长串关键词匹配。模型大了 ~5×(990 MB vs
231 MB),但用户画像里"第一次识别就对"的价值完全值这个存储成本。

**本轮拍板的几个结构性决定**:

1. **Qwen3-ASR 3 段 ONNX 管线保持不合并**:
   `conv_frontend.onnx` → `encoder.int8.onnx` → `decoder.int8.onnx`
   分别是独立的 `ort.InferenceSession`。看似冗余,但拆开的核心理由是
   **为第 27 轮流式识别预留结构** —— 流式需要 encoder 能"分 chunk 喂
   mel",如果一开始就写成"一把梭 `transcribe(wav) → text`"的闭合函数,
   拆开要重写。现在 `encode_audio(mel) → audio_features` 和
   `decoder_step(input_ids, audio_features, caches, cur_len) → logits`
   都是纯函数式、side-effect-free、re-entrant 的,27 轮开工就能直接复用
2. **KV cache 用绝对位置 + 固定大小 buffer**:每层
   `(1, max_total_len=1200, 8, 128)` float32,28 层,prefill 和增量解码
   共用同一套 buffer,`decoder_step` 传入 `cur_len` 告诉 decoder 新 token
   写到 cache 的哪个绝对位置。溢出手动 raise `RuntimeError(
   "cache overflow")`。这种写法也是为 27 轮准备 —— 流式跨 chunk 复用
   cache 不需要重新分配,不需要切换形状
3. **Log-mel 特征提取 100% 自己写,不引入 `scipy`/`librosa`/`transformers`**:
   ~100 行 pure numpy,实现 Whisper 风格的 log-mel(N_MELS=128, N_FFT=400,
   HOP=160, N_SAMPLES=480000 = 30 秒 @ 16 kHz)。Slaney mel scale /
   periodic Hann 窗 / reflect-pad STFT 三个细节都和
   `transformers.WhisperFeatureExtractor` 严格对齐 —— 靠
   `tests/fixtures/whisper_mel_golden_zh.npy` 这个 golden 文件 +
   `np.allclose(rtol=1e-4)` 保底。`scripts/generate_whisper_mel_golden.py`
   是 golden 生成器(一次性工具,依赖 transformers,不上线)
4. **Tokenizer 用 HuggingFace `tokenizers`(Rust BPE ~10 MB),不用
   `transformers` / `sentencepiece`**:
   Qwen3-ASR 仓库里没有 `tokenizer.json`(fast tokenizer 快照),但有
   `vocab.json` + `merges.txt` + `tokenizer_config.json`(含 62 个 added
   tokens)。在 `_tokenizer.py` 里从这三份文件重建 byte-level BPE
   tokenizer,手动注册所有 added tokens。`transformers` 全量包 ~100 MB
   就为了个 tokenizer 太浪费,`tokenizers` 只多 10 MB
5. **Prompt 用 chat template 直接字符串拼**:
   `<|im_start|>system\n<|im_end|>\n<|im_start|>user\n<|audio_start|>{AUDIO_PAD * N}<|audio_end|><|im_end|>\n<|im_start|>assistant\n`
   —— N 是 encoder 输出的 audio token 数量。不走 `AutoTokenizer.apply_chat_template`,也不引入 `jinja2`。上游代码本身也是这么拼的
6. **模型切换走"后台线程 + 原子 swap"热切换**:
   用户在设置页选 0.6B → 1.7B,`WhisperInput._switch_stt_variant` 启后台
   daemon thread,`Qwen3ASRSTT("1.7B").load()`(包括 `_warmup()`),然后
   **原子赋值** `self.stt = new_stt` 再 `gc.collect()` 掉旧 session。
   in-flight transcription 继续用旧 session 因为它们已经拿到引用了,不会
   被打断。状态机由
   `_stt_switch_state: dict` + `_stt_switch_lock: threading.Lock`
   驱动 —— 状态对外通过 `/api/stt/switch_status` 暴露,设置页每 500ms
   轮询 —— 用户能看到"切换中..." → "已切换到 1.7B"的闭环反馈
7. **配置自动迁移,保留**`config.yaml` **向后兼容**:
   `ConfigManager._migrate_legacy(cfg)` 在 `load()` 里跑,检测到
   `engine: sensevoice` 或 `sensevoice:` 块就重写成
   `engine: qwen3` + `qwen3: {variant: "0.6B"}`,然后立即 `save()`
   落盘。老用户升级后不需要手动改 config.yaml

**顺手修掉的一个潜在 bug**:`ConfigManager._deep_merge` 原来用浅 copy,
`DEFAULT_CONFIG` 可以通过返回的 dict 被外部修改(测试串扰才把这个问题
暴露出来 —— `test_generated_yaml_contains_key_sections` 在跑完其它测试
后 fail,因为 `DEFAULT_CONFIG["qwen3"]["variant"]` 被前面的 `mgr.set`
偷偷改掉了)。改成 `copy.deepcopy` 之后根除。

### 开发内容概括

**新包** [src/whisper_input/stt/qwen3/](src/whisper_input/stt/qwen3/)(8 个模块,整包 100% 覆盖):

- [_feature.py](src/whisper_input/stt/qwen3/_feature.py) —— Whisper 风格
  log-mel + `pad_or_trim(audio)`
- [_tokenizer.py](src/whisper_input/stt/qwen3/_tokenizer.py) —— HF
  `tokenizers` 包装,自定义加载 `vocab.json` + `merges.txt` +
  `tokenizer_config.json`
- [_prompt.py](src/whisper_input/stt/qwen3/_prompt.py) —— chat template
  字符串构造
- [_postprocess.py](src/whisper_input/stt/qwen3/_postprocess.py) ——
  `parse_asr_output(raw)` 抽取 `<asr_text>` 之后的内容 + 清洗残留的
  `<|...|>` chat 标记
- [_downloader.py](src/whisper_input/stt/qwen3/_downloader.py) ——
  `download_qwen3_asr(variant)` 调 `modelscope.snapshot_download` 用
  `allow_patterns` 只拉指定 variant
- [_onnx_runner.py](src/whisper_input/stt/qwen3/_onnx_runner.py) ——
  `Qwen3ONNXRunner` 包装 3 个 session,内部 `_inspect_decoder()` 动态
  推断层数/kv_heads/head_dim,暴露 `encode_audio`/`decoder_step`/
  `alloc_decoder_caches`
- [qwen3_asr.py](src/whisper_input/stt/qwen3/qwen3_asr.py) ——
  `Qwen3ASRSTT(BaseSTT)` 主类,`load()` 幂等 + warmup,`transcribe()`
  做 wav→float32 + pad_or_trim + log-mel + encode + prefill +
  `_MAX_NEW_TOKENS=400` 贪心 decode

**STT 工厂重写** [stt/__init__.py](src/whisper_input/stt/__init__.py):
`create_stt(engine, config)` 只认 `engine="qwen3"`,`engine="sensevoice"`
或其它值抛 `ValueError`。lazy import 延续 —— `--help` 不付
numpy/onnxruntime/modelscope 的 import cost。

**配置管理** [config_manager.py](src/whisper_input/config_manager.py):

- `DEFAULT_CONFIG["engine"]` 从 `"sensevoice"` → `"qwen3"`
- 删掉 `sensevoice: {use_itn: True}` 块,新增 `qwen3: {variant: "0.6B"}`
- 新增 `_migrate_legacy(cfg) -> (new_cfg, changed)`,`load()` 里检测到
  就自动 `save()`
- `_deep_merge` 从浅 copy 改成 `copy.deepcopy`(**修了一个潜在 bug**)
- `_generate_yaml` 把示例段从 sensevoice 改成 qwen3

**主控** [__main__.py](src/whisper_input/__main__.py):

- 默认 engine / 文案全部从 sensevoice 改到 qwen3
- 新增 `_stt_switch_lock` / `_stt_switch_state` / `stt_switch_status()` /
  `_switch_stt_variant(new_variant)`,参数绑到 `on_config_changed` 里
  `qwen3.variant` 变化时触发
- `SettingsServer` 构造入参多传一个 `stt_switch_status_getter`

**设置服务** [settings_server.py](src/whisper_input/settings_server.py):
新增 `GET /api/stt/switch_status` 端点,默认返回 `{"switching": False,
 "variant": None, "error": None}`(getter=None 时的 fallback)。

**设置页 UI** [settings.html](src/whisper_input/assets/settings.html):

- 新增"识别模型"下拉(0.6B (快) / 1.7B (更准)),绑
  `saveSetting('qwen3.variant', value)`
- `pollSttSwitchStatus()` 每 500ms 查 `/api/stt/switch_status`,状态
  `switching=true` 时显示"切换中..." + toast,`switching=false` 时根据
  `error` 字段 toast 成功/失败
- config 加载时从 `config.qwen3.variant` 回填下拉初始值

**国际化** 三份 locale(zh / en / fr)各加 6 条 + 删掉 sensevoice.* 残留:
`settings.stt_variant` / `settings.stt_variant_desc` /
`settings.stt_variant_0_6b` / `settings.stt_variant_1_7b` /
`settings.stt_switching` / `settings.stt_switch_done` /
`settings.stt_switch_failed`。`init.download_model` 的 size 数字从 231 MB
改成 990 MB。

**依赖调整** [pyproject.toml](pyproject.toml):

- 删除 `kaldi-native-fbank` / `sentencepiece`
- 新增 `tokenizers>=0.20`(HF Rust BPE,~10 MB)
- `description` / `keywords` / 首行 docstring 换到 Qwen3-ASR

**macOS 卸载补丁** [backends/app_bundle_macos.py](src/whisper_input/backends/app_bundle_macos.py):
`--uninstall` 清理 Qwen3-ASR cache 目录,同时保留对 SenseVoice cache 的
兼容(老用户卸载时一并清)。

**删除**:

- `src/whisper_input/stt/sense_voice.py`
- `src/whisper_input/stt/_wav_frontend.py`
- `src/whisper_input/stt/_tokenizer.py`(老的 SentencePiece 版,不是
  qwen3 子包里的 `_tokenizer.py`)
- `src/whisper_input/stt/_postprocess.py`(老的 rich_transcription_postprocess)
- `tests/test_sense_voice.py`
- `tests/test_postprocess.py`

**文档重写**:

- [CLAUDE.md](CLAUDE.md) —— Project Overview / Commands / Architecture /
  Key Technical Decisions / Dependencies / Upgrading 六节全部重写
- [README.md](README.md) + [README.zh-CN.md](README.zh-CN.md) —— 首段
  模型介绍、下载大小、配置表、技术架构段全改
- [BACKLOG.md](BACKLOG.md) —— 热词条目从"SenseVoice 原生 hot words"重定向
  到"Qwen3-ASR 原生 prompt biasing";流式条目标注为第 27 轮主线 + 说明
  本轮留下的接口钩子
- [tests/fixtures/README.md](tests/fixtures/README.md) —— 引用从
  `SenseVoiceSTT.transcribe()` 改成 `Qwen3ASRSTT.transcribe()`,新增
  `whisper_mel_golden_zh.npy` 说明章节
- [.github/workflows/build.yml](.github/workflows/build.yml) —— ModelScope
  cache key 从 `modelscope-sensevoice-v1` 改成 `modelscope-qwen3-asr-v1`

### 额外产物

- **239 条单测**(比 25 轮末尾增长 ~50 条,整体覆盖率从 51% → 61%):
  - `tests/test_qwen3_feature.py` —— log-mel / pad_or_trim + golden 对齐
  - `tests/test_qwen3_tokenizer.py` —— 加载 + encode/decode + 特殊
    token ID
  - `tests/test_qwen3_prompt.py` —— chat template + audio_pad 数量
  - `tests/test_qwen3_postprocess.py` —— `<asr_text>` 抽取 / 空输入 /
    残留 token 清洗
  - `tests/test_qwen3_downloader.py` —— variant 验证 + modelscope mock
  - `tests/test_qwen3_runner.py` —— 用真 0.6B 模型 introspection +
    encode + prefill + KV cache 写入/保留/溢出(需要
    `qwen3_0_6b_model_dir` fixture,cache 缺失时 skip)
  - `tests/test_qwen3_asr.py` —— end-to-end `zh.wav` 推理,断言包含
    "先帝创业未半而中道崩殂"
  - `tests/test_stt_factory.py` —— `create_stt` 路由
  - `tests/test_main_stt_switch.py` —— 8 条热切换场景(同 variant 空操作 /
    成功 / 失败保留旧 stt / 并发拒绝 / on_config_changed 触发 / 切换中
    状态轮询)
- **spike 脚本保留** [scripts/spike_qwen3_onnx.py](scripts/spike_qwen3_onnx.py) ——
  enum ONNX graph 接口、dry-run forward。不上线,留作未来 ONNX 新版本
  回归诊断
- **golden 生成脚本** [scripts/generate_whisper_mel_golden.py](scripts/generate_whisper_mel_golden.py) ——
  一次性工具,依赖 `transformers`(临时用 `--with transformers` 跑,不
  加入项目依赖)
- **ConfigManager 潜在 bug 修复**:`_deep_merge` 从浅 copy → `copy.deepcopy`
- **`install.sh` 文案更新**:`msg_step_init` 里 "231MB" 改成 "Qwen3-ASR 0.6B ~990 MB,可能需要几分钟",避免新用户按旧数字估错时间
- **跑测期间新发现 → 4 条 BACKLOG 条目**(都没当场做,避免本轮 scope 蔓延):
  - **[按需可视化下载](BACKLOG.md)**:1.7B 当前点选即触发 2.4 GB 无反馈下载,
    UI 上选中状态提示不足。改造为"未下载时 disabled + 显式下载按钮 + 进度条"
    是独立一轮的事
  - **[`Qwen3ASRSTT.load()` 细粒度日志](BACKLOG.md)**:本轮 loading/loaded
    一对事件太粗,snapshot_download / session init / warmup 各占多少时间
    完全是黑盒。先加日志再谈优化
  - **[麦克风检测按需启动](BACKLOG.md)**:设置页打开即占麦(macOS 菜单栏橙
    点常亮,有偷听误解),应该改成按钮触发 + 再点停止
  - **[`-v` / `--version` CLI 选项](BACKLOG.md)**:CLI 没有版本号显示,成熟工具
    标配缺失

### 最终验收数据

- `uv run ruff check .` —— All checks passed
- `uv run pytest --no-cov -q` —— 239 passed, 1 warning
- `stt/qwen3/` 子包 8 个模块全部 **100%** line coverage
- 整体覆盖率 **61%**(baseline 51%,PLAN 目标 70%)
- `tests/fixtures/zh.wav` 经 0.6B 识别输出:
  "先帝创业未半而中道崩殂,今天下三分,益州疲弊,此诚危急存亡之秋也。"(零错字)

## 局限性

1. **整体覆盖率没达到 PLAN 的 70% 目标,停在 61%**。差距几乎全部集中在
   `__main__.main()` 的 CLI 解析 / 托盘启动 / 浏览器打开 / 信号处理
   ~230 行 —— 这部分 25 轮之前就没测,26 轮也没顺手补。真要推过 70%
   至少还要再写 15-20 条偏集成风格的 `main()` 测试(要 patch 大量模块)。
   本轮评估收益 / 成本比不高 ——PLAN 里这个 70% 目标定下来的时候没意识
   到基线已经是 51% 且剩余差距都集中在入口编排,所以**接受 61% 作为本
   轮最终数字,不做补全**。未来有需要再开小轮单独做
2. **0.6B vs 1.7B 热切换的真实验证还没跑**(Step 17 待用户本地做)。
   单元测试用 threading.Event + slow mock 校验过并发安全、状态机正确性,
   但"切换时 RSS 是否真的不累积"这种系统级观察单测没法覆盖,需要用户
   在真机上按"0.6B → 1.7B → 0.6B → 1.7B"循环几次后看 Activity Monitor
3. **Wasser1462 参考代码的 license 仍未明确**。本轮代码完全是 reference-only
   看过之后自己写的,不直接 port 他的代码,但发布前我还是应该 open 一个
   issue 问清楚他代码的 license(他仓库 README 没写)。这不阻塞本轮
   merge,阻塞下一次 PyPI release
4. **没有 HuggingFace fallback**:ModelScope 崩了应用下载失败。这是 PLAN
   明确接受的限制,不是遗漏
5. **Qwen3-ASR 的 prompt biasing / hotwords 能力没用上**:本轮 system
   prompt 留空。留第 28 轮
6. **log-mel golden 只覆盖 `zh.wav` 一条样本**。理论上不同语种 / 不同
   采样特性可能跑出 golden 漏掉的 bug,但 Whisper 的 feature extractor
   本身是语种无关的(纯 DSP,不分 mel 形状),golden 对齐到 rtol=1e-4
   已经足够说明实现正确

7. **冷启动慢**(用户实测反馈):首次 `uv run whisper-input` 到 ready 要
   十几秒。拆开看:`modelscope.snapshot_download` 有 cache 也会走 manifest
   校验(~1-2s)+ 3 个 ONNX session 依次加载(decoder.int8.onnx 单文件
   ~700 MB,加载 5-8s 不等)+ `_warmup()` 跑一次完整 prefill(~1s)。
   `--no-preload` 会把这坨推到首次按热键时触发,视感更差。短期不优化 ——
   冷启动慢是换 LLM 式 ASR 的必然代价(对比 SenseVoice 加载 231 MB 是
   快得多)。未来可优化方向:warmup 裁剪只跑 encoder / snapshot_download
   有 cache 时走 `local_files_only`

8. **松手后粘贴有 ~2s 延迟**(用户实测反馈):batch 模式下 `transcribe()`
   串行做 encoder(对 pad_or_trim 到 30s 的 mel 跑完整前向,~1s)+ decoder
   贪心循环(10s 语音约 30-40 token,每步 5-10ms,总 ~200-400ms)。
   Encoder 是延迟大头。**这条局限性的根治方案就是第 27 轮流式识别**——
   streaming 下 encoder 按 chunk 吃 mel、decoder 边听边解码,松手时大部分
   工作已经 done,用户感知延迟趋近零。本轮的 3 段 ONNX + 绝对位置 KV
   cache 结构就是专为此预留的

## 后续 TODO

- **分支合并时机**:本轮落在 `feat/qwen3-stream`,不直接合 master。预期
  路径是 27 轮流式识别在这条分支上继续迭代,流式做好后整条分支一起 merge
  回 master。这样用户第一次拿到 Qwen3-ASR 版本时就同时享受"识别更准 +
  松手无感延迟",不会经历"识别变准但等待变长"的中间态
- **第 27 轮:流式识别**(BACKLOG 已标注为主线)—— 本轮 3 段 ONNX + 绝对
  位置 KV cache 是专为它准备的。encoder 分块 spike 先做半天
- **第 28 轮:hotwords / prompt biasing** —— Qwen3-ASR 天然支持的结构
  性能力。做之前先 spike:int8 量化后 prompt 引导是否还有效
- **覆盖率补全到 70%**:专门针对 `__main__.main()` 补一批集成测,顺便把
  `hotkey_*` 的 `_listen_loop` / `start` / `stop` 用 fake Listener 驱动测
  (BACKLOG 已记)
- **多语种 fixture**:当前 `tests/fixtures/` 只有 zh,可以加 en / ja / ko
  / yue 各一条短样本,顺便校验 Qwen3-ASR ONNX int8 在非中文上是否掉点
- **Wasser1462 license 澄清**:给他仓库开 issue 问 license。这条不进
  BACKLOG —— 它是"release 前必做"而非"开新轮做",搞定了就忘掉
