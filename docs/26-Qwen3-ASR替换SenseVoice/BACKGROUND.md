# 背景知识:音频的 Token 化与 ASR 推理链路

本文档供后续开发人员(含未来的 agent)理解本轮为什么要做"替换 Kaldi fbank
为 Whisper log-mel"、"自己写一个特征提取器"这类看起来突兀的工程任务。

如果你对音频处理完全不熟,只做过文本的 LLM——这份背景知识就是给你准备
的。

## 为什么音频需要"特征提取"这一步

一段 5 秒的 16kHz mono 音频是 80000 个 int16 采样点。直接把这些采样点
喂给神经网络有两个问题:

1. **信息稀疏**:相邻采样点高度相关,真正重要的信息(频率构成、能量
   分布、音高)需要模型从波形中自行学习。让神经网络从原始波形学这些
   低级特征,要么需要超大模型,要么效果差
2. **序列长度爆炸**:80000 个 token 对 transformer attention 是灾难
   (O(n²) 复杂度),实际无法训练

所以几乎所有 ASR 系统(Whisper、Paraformer、SenseVoice、Qwen3-ASR)都先
把原始波形转成一张"特征图",类似 2D 图片:

- 横轴:时间(每 10ms 一帧)
- 纵轴:频率(几十到几百个频率 bin)
- 值:该时刻该频率的能量强度

这张图叫 **spectrogram**。再经过 mel scale 变换 + 取对数 + 归一化,就
成了 **log-mel spectrogram**——现代 ASR 最主流的输入形式。

5 秒音频从 80000 个采样点压缩成 `500 帧 × 80 或 128 维` ≈ 40000-64000
个浮点数,**数据量降一个数量级,每个数字都有物理意义**。

## 对应 LLM 世界的类比

| LLM 世界              | ASR 世界                             |
|-----------------------|--------------------------------------|
| 原始字符串            | 原始波形(int16 PCM)               |
| **Tokenizer**(BPE)   | **特征提取器**(fbank / log-mel)   |
| Token ID 序列         | Log-mel spectrogram 张量             |
| Embedding 层          | conv_frontend(CNN 前端)            |
| Transformer 层        | Encoder                              |
| Decoder + LM head     | Decoder + LM head(Qwen3-ASR 一致)  |

**关键认知:特征提取器 = 音频世界的 tokenizer**。跟 BPE vs SentencePiece
类似,不同 ASR 模型家族使用不同的特征提取约定,**训练时用哪套推理时
必须严格对齐**,否则数值分布不同、识别质量崩盘。

## Kaldi fbank vs Whisper mel——我们关心的两套约定

本轮涉及的两个模型家族正好代表了两套主流约定。

| 维度              | Kaldi fbank(SenseVoice 用)       | Whisper mel(Qwen3-ASR 用)         |
|-------------------|-----------------------------------|-------------------------------------|
| Mel bin 数        | **80**                            | **128**                             |
| 帧长 / 帧移       | 25ms / 10ms                       | 25ms / 10ms                         |
| 预加重            | 有(系数 0.97)                  | 无                                  |
| Dithering         | 有(防 log(0))                  | 无                                  |
| 窗函数            | Povey window(Kaldi 特制)       | Hann window                         |
| Mel scale         | Slaney / HTK                      | HTK + Whisper 特殊归一化            |
| 后处理            | CMVN(均值方差归一)+ LFR(低帧率拼接) | 截断 3000 帧 + log clip            |
| 数值范围          | 大致 [-5, 5]                     | 大致 [-1, 0]                        |
| 实现库            | `kaldi-native-fbank`(C++,小)  | `librosa` 或自己手写                |

两者都是**纯信号处理算法**,没有 learnable 参数。步骤相似(分帧 → FFT
→ 幅值 → mel filter bank → log),但每一步的参数都不同,输出数值差异
大。不能互换。

## 音频的完整 ASR 链路(Qwen3-ASR 为例)

以下是本轮 spike 中实际看到的 ONNX 拆分,跟大多数现代 ASR 一致:

```
┌─────────────┐  16kHz mono PCM (float32)
│  Mic input  │
└──────┬──────┘
       ↓
┌─────────────────────────────────────┐
│ Log-mel spectrogram 提取器         │  Whisper 风格,纯 numpy
│ (Python 层实现,无 ML 框架依赖)   │  输出: [n_frames, 128]
└──────┬──────────────────────────────┘
       ↓ [1, n_frames, 128] float32
┌─────────────────────────────────────┐
│ conv_frontend.onnx                  │  3 层 CNN 降采样
│                                     │  输出: [1, n_audio_tokens, 896]
└──────┬──────────────────────────────┘
       ↓
┌─────────────────────────────────────┐
│ encoder.int8.onnx                   │  Transformer encoder
│ (对音频建模,跨时间 attention)     │  输出: [1, n_audio_tokens, 1024]
└──────┬──────────────────────────────┘
       ↓ audio_features
┌─────────────────────────────────────┐
│ decoder.int8.onnx                   │  28 层自回归 Transformer decoder
│ (自回归生成文字 token,            │  输出每步 logits + KV delta
│  cross-attention 读 audio_features) │
└──────┬──────────────────────────────┘
       ↓ token_id
┌─────────────────────────────────────┐
│ Tokenizer (Qwen2 BPE)               │  token_id → UTF-8 文本片段
└──────┬──────────────────────────────┘
       ↓
     识别文本
```

对做过 LLM 的人来说,**decoder 之后的部分你完全熟悉**——就是个标准的
自回归 transformer,跟 Qwen / Llama 用 KV cache 生成文字一模一样。唯一
不同是 decoder 的 cross-attention 读的不是"上一层的 hidden states",而
是 encoder 输出的 audio_features。这就是 encoder-decoder transformer 的
标准结构,Whisper 也是这个架构。

## SenseVoice 为什么是"非自回归"的异类

顺便说一下 SenseVoice 的架构,解释为什么我们上一轮会说"SenseVoice 本身
不支持流式":

SenseVoice 是 **encoder-only**,没有 decoder。它的预测方式是 CTC
(Connectionist Temporal Classification)——encoder 对每一帧预测一个
label(可能是某个 token 或 "blank"),然后用 CTC 规则把帧级 label 序列
去重压成最终文本。

这意味着:

1. **非自回归**:所有 token 一次性一把出来,没有"一个一个生成"的过程。
   这是它快的原因(RTF 0.1x),但也意味着没有"输出流"的可能性
2. **上下文扁平**:encoder 完整跑一遍得到帧级 logits,CTC 解码是后处理
   步骤

所以 SenseVoice 只能做"切段后逐段离线推理"这种伪流式,且每段切点处的
标点会退化成句号(第 1 轮 SUMMARY 分析过)。Qwen3-ASR 是 encoder-decoder
的标准自回归结构,**天然支持 token 级流式**——这就是本轮为什么要替换
模型的根本原因。

## 流式 ASR 的两种策略(背景理解用)

做真·流式 ASR 有两种思路,对应两种模型架构:

### 1. CTC 流式(SenseVoice、streaming-sensevoice 项目用的思路)

- 模型本身是 encoder-only + CTC,自带帧级对齐
- 流式方式:encoder 每接收一小段音频就跑一次,CTC 解码增量输出
- 代价:需要把 encoder 改成能 chunk 接收的形式(滑动窗口 encoder)
- 适合:非自回归架构

### 2. Encoder-decoder 流式(Qwen3-ASR、Whisper 改造版用)

- 模型是 encoder + 自回归 decoder
- 流式方式:
  - Encoder 分段跑(每 2 秒一次),累积 audio_features
  - Decoder 每次用"已 commit 的 prefix + 回退 N 个 token"作为起点,重新
    自回归生成,直到尾部 N 个 token 稳定
  - 只 commit "已被连续两轮再次确认的" token,尾部留着
- 代价:decoder rollback 逻辑需要自己实现,KV cache 管理复杂
- 适合:自回归架构

**本轮选的是策略 2**。策略细节在 PLAN.md 里的"流式状态机"章节。

## 进一步阅读

- Whisper 论文 `Robust Speech Recognition via Large-Scale Weak Supervision`
  的附录部分有 log-mel 提取的官方数学定义,是我们手写特征提取器的
  ground truth
- Qwen3-ASR 技术报告(arXiv 2601.21337)介绍了模型结构
- antirez/qwen-asr 的 C 代码是工业级的 encoder-decoder 流式实现参考
- pengzhendong/streaming-sensevoice 是 CTC 流式的典型实现(我们之前否决
  的那条路线)
