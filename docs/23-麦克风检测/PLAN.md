# 实现计划：设置页面集成麦克风检测功能

## 架构决策

- 实时波形 + 录音 + 回放 = 完全在浏览器端用 Web Audio API / MediaRecorder 实现，无需后端录音
- 后端只加一个不阻塞的 `GET /api/audio-devices`（调用 `sd.query_devices()`，毫秒级）
- 不引入 ThreadingHTTPServer，不改动 server 框架，不引入新 Python 依赖

## 涉及文件

- `src/whisper_input/settings_server.py`（+1 路由 + 1 handler）
- `src/whisper_input/assets/settings.html`（+麦克风检测卡片 + JS）
- `src/whisper_input/assets/locales/zh.json`
- `src/whisper_input/assets/locales/en.json`
- `src/whisper_input/assets/locales/fr.json`

## 步骤

### 1. settings_server.py

在 `do_GET` 的 elif 链（404 之前）添加：
```python
elif self.path == "/api/audio-devices":
    self._handle_audio_devices()
```

新增 handler：
```python
def _handle_audio_devices(self) -> None:
    import sounddevice as sd
    try:
        devices = sd.query_devices()
        default_input = sd.default.device[0]
        result = [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"],
             "is_default": (i == default_input)}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
    except Exception:
        result = []
    self._send_json({"devices": result})
```

### 2. settings.html

在「高级设置」卡片之后插入「麦克风检测」卡片，包含：
- 设备列表行（从 /api/audio-devices 加载）
- Canvas 波形实时显示（Web Audio API）
- 录音/停止按钮（MediaRecorder API）
- 回放 `<audio>` 元素（初始隐藏）

JS 逻辑：
- `initMicCheck()`: 加载设备 + 请求 getUserMedia + 启动波形动画
- `drawMicWave()`: requestAnimationFrame 循环绘制 AnalyserNode 波形
- `toggleMicRecord()`: 切换 MediaRecorder 录音/停止，停止时创建 Blob URL 播放
- `loadConfig()` 末尾调用 `initMicCheck()`

### 3. i18n

新增 8 个 key（zh/en/fr 三个文件）：

| key | zh | en |
|-----|----|----|
| `settings.mic_check` | 麦克风检测 | Microphone Check |
| `settings.mic_devices` | 可用设备 | Available Devices |
| `settings.no_mic_devices` | 未检测到麦克风 | No microphone detected |
| `settings.mic_permission_hint` | 授权麦克风权限后自动显示实时波形 | Grant mic permission to see live waveform |
| `settings.mic_record` | 录音回放 | Record & Play Back |
| `settings.mic_record_desc` | 点击录制，再次点击停止并回放 | Click to record, click again to stop and play back |
| `settings.mic_record_btn` | 录音 | Record |
| `settings.mic_stop_btn` | 停止 | Stop |

## 验收测试

1. 设置页面出现「麦克风检测」卡片，设备列表显示麦克风名称
2. 浏览器弹出麦克风权限请求，授权后 canvas 波形实时滚动
3. 点「录音」→ 说几句话 → 点「停止」→ `<audio>` 控件出现并自动播放
4. 无麦克风时设备列表显示红色提示，点「录音」显示 toast
5. 切换语言后所有新增文本正确翻译
