# 实现计划：流式识别（未成功，记录尝试过的方案）

## 方案一：伪流式（已实现，已废弃）

定期对累积音频运行 SenseVoice 完整推理，用 BackSpace 替换上次中间结果。

- 失败：BackSpace 替换机制脆弱，跨会话状态泄漏，文字错乱

## 方案二：Paraformer-streaming 真流式（已实现，已废弃）

使用 FunASR Paraformer-streaming 模型做 chunk-based 流式识别，增量追加文字。

- 失败：字符重复、无流式输出、无标点、识别质量差

## 方案三（推荐，未实现）：Qwen3-ASR

使用 Qwen3-ASR-0.6B/1.7B，自带标点 + 流式 + 高准确率。需引入 vLLM 后端。

详见 SUMMARY.md 中的调研结论。
