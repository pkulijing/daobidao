# 实现计划：Linux 语音输入工具

## 调研结论

### 现有方案

- 微信输入法 Linux 版：有文字输入，无语音模块
- 豆包输入法：无 Linux 版
- 搜狗 Linux 版：语音模块被阉割
- nerd-dictation：英文为主，CJK 输出有 bug
- Vocalinux / Voxtype 等开源项目：较新，中文体验一般

**结论：无现成好方案，自行实现。**

### 模型选择

| 模型 | 类型 | 中文准确度 | 速度 | 说明 |
|---|---|---|---|---|
| SenseVoice-Small | 本地 | 最佳（超 Whisper-v3） | 非自回归，极快 | Apache 2.0，推荐本地首选 |
| faster-whisper large-v3 | 本地 | 很好 | 自回归，较慢 | 生态成熟 |
| 豆包 Seed-ASR | 云端 | 业界最强中文 | 快 | 有免费额度 |

**决定：本地用 SenseVoice-Small，云端用豆包 ASR。**

## 技术方案

### 架构

```
按住热键 → 录音(sounddevice) → 松开热键 → STT识别 → 剪贴板粘贴到焦点窗口
```

### 模块设计

1. **hotkey.py** — 热键监听
   - 使用 `evdev` 直接读取键盘设备事件
   - 支持区分左右修饰键（Left/Right Ctrl, Alt, Meta）
   - 需要 root 权限或 input 组权限

2. **recorder.py** — 音频录制
   - 使用 `sounddevice` 录制 16kHz 单声道音频
   - 按住开始、松开停止，输出 WAV 格式

3. **stt_sensevoice.py** — 本地 SenseVoice 引擎
   - 基于 FunASR 的 `AutoModel` 加载 `iic/SenseVoiceSmall`
   - 首次加载约 2-3 秒，之后推理极快
   - 支持自动语言检测（中/英/日/韩/粤）

4. **stt_doubao.py** — 豆包云端引擎
   - 使用火山引擎语音识别 WebSocket API
   - 需要 app_id 和 access_token（控制台获取）

5. **input_method.py** — 文字输入
   - 通过 `xclip` 写入剪贴板 + `xdotool` 模拟 Ctrl+V 粘贴
   - 粘贴前保存原剪贴板内容，粘贴后恢复

6. **main.py** — 主入口
   - 加载 YAML 配置，串联各模块
   - 系统托盘图标（pystray），支持右键切换引擎
   - 提示音反馈

### 关于 Fn 键

Fn 键由键盘固件拦截，不发送键码到操作系统，无法被软件捕获。使用右 Ctrl 等替代。

### 配置

通过 `config.yaml` 配置引擎、热键、音频参数等，支持命令行参数覆盖。
