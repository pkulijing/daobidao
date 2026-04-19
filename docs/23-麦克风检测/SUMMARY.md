# 第 23 轮开发总结：设置页面集成麦克风检测

## 背景

### 问题表现

当前机器上没有麦克风时，程序不会报错，而是录制到空白音频，SenseVoice 对空白音频会输出一个奇怪的韩语字符（实际上是语言标签噪声）。这个问题在 Linux 系统上尤为突出，因为 Linux 服务器/桌面通常没有内置麦克风。

### 影响

用户要去外部网站（如 mic-test.com）才能检测麦克风是否正常工作，体验很差：
- 不知道自己机器上有没有麦克风
- 即使有，也不知道是否被系统正确识别
- 更不知道麦克风实际的音质表现

## 实现方案

### 关键设计

1. **浏览器优先架构**：实时波形 + 录音 + 回放完全在浏览器端用 Web Audio API / MediaRecorder 实现，后端几乎不参与。这是最正确的方向 —— mic-test.com 也是这样做的
2. **后端仅一个非阻塞 API**：`GET /api/audio-devices` 用 `sounddevice.query_devices()` 列举输入设备，毫秒级响应。**不引入 ThreadingHTTPServer**，保持原有 server 框架不变
3. **不引入新 Python 依赖**：`sounddevice` 已是项目依赖
4. **关闭浏览器默认音频处理**：`getUserMedia` 默认会开启 `echoCancellation` / `noiseSuppression` / `autoGainControl`，这些算法会让语音听起来断续失真。在显式关闭后，录下来的音频才是硬件直出的原始音质
5. **显式指定 MediaRecorder bitrate**：默认 bitrate 在部分浏览器下过低，显式设为 128 kbps + Opus 编码保证音质

### 开发内容概括

**后端** ([settings_server.py](../../src/whisper_input/settings_server.py))：
- 新增 `GET /api/audio-devices` 路由
- 新增 `_handle_audio_devices()` handler：调用 `sd.query_devices()` 过滤输入设备，返回 `[{index, name, channels, is_default}, ...]`

**前端** ([settings.html](../../src/whisper_input/assets/settings.html))：
- 新增「麦克风检测」卡片，位于「高级设置」之后、操作按钮之前
  - 设备列表行：从 `/api/audio-devices` 加载，无设备时红色提示
  - Canvas 实时波形：页面加载后自动请求麦克风权限，授权后用 `AnalyserNode.getByteTimeDomainData()` + `requestAnimationFrame` 在 canvas 上实时绘制
  - 录音/停止按钮：`MediaRecorder` 录音，停止后 `<audio>` 控件出现并自动播放
- JS 函数：`initMicCheck()`、`drawMicWave()`、`toggleMicRecord()`
- `getUserMedia` 显式关闭 `echoCancellation`/`noiseSuppression`/`autoGainControl`，采样率 44100
- `MediaRecorder` 指定 `audio/webm;codecs=opus` + 128 kbps

**国际化**（zh.json / en.json / fr.json）：
- 新增 8 个翻译 key：`settings.mic_check`, `settings.mic_devices`, `settings.no_mic_devices`, `settings.mic_permission_hint`, `settings.mic_record`, `settings.mic_record_desc`, `settings.mic_record_btn`, `settings.mic_stop_btn`

### 额外产物

无。本轮纯功能开发，没有生成测试用例或调试脚本（UI 交互逻辑现有测试套不覆盖）。

## 局限性

1. **没有修复「无麦克风时识别出奇怪韩语字符」的根本问题**：本轮只是给用户提供一个**主动检测**工具，让他们自己判断麦克风是否正常。但应用主流程在无麦克风 / 空白音频时仍然会走一遍 SenseVoice 识别并输出韩语垃圾。后续可以考虑在 `recorder.py` 加 RMS 阈值判断，检测到空白音频直接跳过识别
2. **首次加载需要浏览器权限弹窗**：每次打开设置页面都要重新授权麦克风（浏览器安全策略），用户体验上多一个点击
3. **录音格式是 webm 而非 wav**：浏览器 MediaRecorder 只原生支持 webm/ogg，如果未来要做"把录音送去 SenseVoice 测试识别效果"这类功能，还得再加一步格式转换
4. **UI 样式相对朴素**：用户已经提到过设置页面"有点太裸了"，本轮没有顺带改善视觉，保持在最小改动范围内

## 后续 TODO

1. **空白音频跳过识别**：在 `recorder.py` 或 `__main__.py` 的主流程加一个音量阈值判断，如果整段录音的 RMS 低于某个值（如 < 50），不调用 SenseVoice 直接提示用户"未录到音频"。这能从根本上消除韩语垃圾输出
2. **麦克风不可用时的启动提示**：应用启动时调一次 `sd.query_devices()`，如果没有输入设备，托盘 tooltip 或日志给出明确提示，引导用户检查硬件 / 去设置页做检测
3. **设置页面视觉升级**：独立一轮做，考虑用 Tailwind / 轻量 CSS 框架或自行改写样式，让整体视觉更现代
