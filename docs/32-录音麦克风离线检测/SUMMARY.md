# 第 32 轮开发总结：录音时实时检测麦克风离线

## 背景

23 轮在设置页加了**被动式**麦克风检测（用户主动点「检测」才知道有没有麦克风），但**主流程录音时**还是机械执行 —— 拔了麦之后用户按热键说话，程序照常走录音 → STT → paste，最后 paste 出空字符串或 Qwen3-ASR 的幻觉 token（"嗯"、"谢谢观看"）。用户得反复试才意识到是麦克风问题，影响信任感。

本轮目标：按下热键瞬间就识别出"麦克风离线"，浮窗短暂显红 + 不录不 paste，跨蓝牙耳机 / USB 麦 / 系统音频路由切换都能兜住。

## 实现方案

### 关键设计

1. **Linux 上 `pactl list sources` 是唯一权威**（这是本轮的核心发现）

   原计划"`sd.query_devices(kind='input')` 一把抓"在 PipeWire 实测时**完全失效**：物理麦拔了之后 PipeWire 仍会通过 sounddevice 暴露 `default` 虚拟设备，`max_input_channels=64`，`InputStream.start()` 也成功，60 次 callback 都干净送达，**只是音频数据全 0**。Chrome 的 `getUserMedia` 之所以能正确判定"未检测到麦克风"，是因为它走的是 PipeWire 元数据层，看 `Ports:` 字段里的 jack-detect 状态：

   ```
   alsa_input.pci-0000_00_1f.3.analog-stereo
       analog-input-front-mic: Front Microphone (..., not available)
       analog-input-rear-mic: Rear Microphone (..., not available)
   ```

   `pulseaudio-utils`（含 `pactl`）已经是 `setup.sh` / `install.sh` 的 APT 依赖，所以这一升级**不增加新的运行时依赖**，只是把"假装可选"的 pactl 升级为"必需"。

2. **macOS / 其他平台仍走 `sd.query_devices`**

   CoreAudio 在 MacBook 主流场景下能反映真实硬件状态（内置麦永远在，蓝牙 / USB 麦拔了会自动 fallback）；Mac mini / Mac Pro 等无内置麦的桌面机会有跟 PipeWire 同样的"虚拟 default 欺骗"问题，但本轮没 Mac 测试机，留 follow-up（首选 `system_profiler SPAudioDataType`）。

3. **probe 失败 → 浮窗错误态；去抖按 reason 分**

   `WhisperInput._show_mic_offline_warning` 统一处理。**去抖只对 `device_lost` 生效**（callback 被动触发，蓝牙抖动可能 1s 多次，5s 内只弹一次防刷屏）；`probe_failed` / `stream_error` 是用户主动按热键，**每次都弹**——否则用户按下没浮窗、没声音，跟程序卡死区分不开（实测教训）。

   `_mic_offline_during_recording` flag 让 `_do_key_release` 早退跳过 paste，**同时立即调 `overlay.hide()`** 让错误浮窗松手就消失，跟正常蓝色浮窗的 release → hide 行为对齐（2.5s 自动 hide 仅作"用户没松手就没动作"的兜底）。

   浮窗 `show_error()` 方法画法：红色药丸 #DC2626 + 麦克风白色对角斜线。

4. **中途断开监控保留代码但 Linux 上失效**

   原计划 callback 连续 5 次 `input_overflow` 升级 device_lost，PipeWire 给的是**完美静音流**所以这条监控**抓不到**。代码保留服务于 macOS 某些 PortAudio 版本 + 纯 ALSA + overload 归因，Linux + PipeWire 用户的"按住录音中拔耳机"场景靠"下次按键 probe 兜底"降级。

5. **复用现有 worker 线程**：probe（pactl subprocess ~50ms）和 stop（PortAudio 兜底 0.5s）都跑在 `daobidao-event-worker`，热键回调线程零阻塞，22 轮的死锁约束不破。

### 开发内容概括

**recorder.py**：

- 新增 `MicUnavailableError(reason, detail)`：`reason ∈ {"probe_failed", "stream_error", "device_lost"}`
- 新增 `PactlUnavailableError`：区分"pactl 不可用"和"pactl 看到没麦"
- 新增 `_check_pactl_input_available() -> bool`：解析 `pactl list sources` 的 Ports 字段
- 新增 `AudioRecorder.probe()`：Linux 走 pactl，其他走 query_devices + 200ms timeout
- 新增 `AudioRecorder.set_stream_status_callback(cb)`：让 callback 把 device_lost 信号 enqueue 给 WhisperInput
- 新增 `_stop_stream_with_timeout(timeout=0.5)`：daemon 线程 + Event 兜底防 PortAudio hang（24 轮教训）
- callback 升级：连续 `_OVERFLOW_DEVICE_LOST_THRESHOLD=5` 次 input_overflow 升级 device_lost；`start[_streaming]()` 用 try/except 把 `sd.PortAudioError` / `OSError` 转 `MicUnavailableError(stream_error)`

**`__main__.py`**：

- `WhisperInput.__init__` 新增 `_last_mic_warning_at` / `_mic_warning_cooldown_s=5.0` / `_mic_offline_during_recording`，并连上 recorder.set_stream_status_callback
- `_do_key_press` 第一步加 probe，外加 try/except 包 `recorder.start[_streaming]()`
- `_do_key_release` 顶部加早退：`_mic_offline_during_recording=True` → 直接 return，不走 stop / paste
- 新增 `_show_mic_offline_warning(reason, detail)`：5s 去抖 + 写日志 + 调 overlay.show_error()
- 新增 `_on_stream_status_signal(flag)`：PortAudio 线程的 lightweight enqueue 入口
- 新增 `_handle_device_lost(flag)`：worker 线程清状态 + 弹浮窗

**overlay_linux.py + overlay_macos.py**：

- 新增 `show_error(message)`：红色药丸 + 麦克风白色对角斜线，2.5s 后 GLib.timeout / NSTimer 自动 hide
- `show()` / `hide()` 取消挂起的 error timeout 防 race
- `set_level()` 在 `_in_error_state` 时直接 return，避免 RMS 把红色药丸刷成跳动条

**locales/{zh,en,fr}.json**：3 个新 key（`main.mic_offline_title` / `main.mic_offline_hint_settings` / `main.mic_lost_during_recording`）

### 额外产物

- 新增 [tests/test_recorder_probe.py](../../tests/test_recorder_probe.py)：18 个用例，分两组（Linux/pactl 路径 + 非 Linux/query_devices 路径），覆盖 pactl 输出解析的各种边界（USB 麦 availability unknown / 全部 not available / 无 alsa_input / .monitor 排除 / pactl 不存在 / nonzero / 超时）
- 扩展 [tests/test_recorder_streaming.py](../../tests/test_recorder_streaming.py)：+12 个用例（连续 overflow 升级 / 单次不报 / underflow 不报 / 跨 session 重置 / start 抛错转 MicUnavailableError / stop hang 兜底）
- 扩展 [tests/test_main_streaming.py](../../tests/test_main_streaming.py)：+9 个用例（probe 失败跳过录音 / release 早退 / 5s 去抖 / device_lost 流式&离线两路径 / 5s 后恢复）
- PLAN.md 修订记录：§4 加了"修订前的 query_devices 方案为什么弃用"小节，作为 PipeWire 教训的决策档案

## 局限性

1. **macOS 边角场景未覆盖**：Mac mini / Mac Pro 等无内置麦桌面机拔 USB 麦后 query_devices 可能返回 `CADefaultDeviceAggregate-xxxx-x` 占位设备，跟 PipeWire 同样问题。本轮没 Mac 测试机不修。BACKLOG 已加 follow-up。

2. **Linux + PipeWire 中途断开监控失效**：录音中拔麦时 PipeWire 继续发静音流不报 status flag，callback 那条监控抓不到。降级路径是"用户下次按键时 probe 兜住"——这意味着"按住录音 5s 中途拔耳机"这一次仍会录到 5s 静音，松手 paste 出空字符串/幻觉 token（这一段还会被 STT 处理一遍）。BACKLOG 已加"在 Linux 起 daemon 线程周期 pactl 探测"的 follow-up。

3. **空白音频幻觉本轮不解决**：BACKLOG 里第 90 行就标了，跟"麦克风离线"是不同问题（麦在线但用户没说话也会幻觉）。需要 RMS 阈值 / VAD / 静音过滤，单开一轮。

4. **pactl 500ms subprocess timeout 可能不够**：本机实测 < 50ms，留了 10× 裕量。极端情况下（比如 PipeWire 服务正在重启、systemd dbus 拥堵）可能超过。超时分支会抛 `PactlUnavailableError` → 转 `MicUnavailableError` → 用户看到红色浮窗，行为合理但归因可能误导（看起来像"pactl 没装"实际是"调用慢"）。如果实测中频繁触发，把 `_PACTL_TIMEOUT_S` 提成 config 项。

5. **错误浮窗只用颜色 + 斜线，不渲染文案**：120×34 太窄，文案只走日志。视觉信息够了（红色 + 斜线 = 麦克风离线），但用户得看日志才知道是 probe_failed 还是 device_lost；如果将来想加 tooltip 或者扩成更大尺寸的 toast，单开一项。

## 后续 TODO

1. **macOS 替代 query_devices**：用 `system_profiler SPAudioDataType` 解析 audio device 列表（首选，对称 pactl 方案，无新依赖），或 `pyobjc-framework-CoreAudio` 调 `AudioObjectGetPropertyData(kAudioHardwarePropertyDevices)`（原生最准但要加依赖）。BACKLOG 已加。

2. **Linux 中途断开监控**：在 Linux 起 daemon 线程，录音期间每 ~500ms 调一次 pactl 看端口可用性，翻 false 时通过 `_event_queue` 升级 device_lost。BACKLOG 已加。

3. **空白音频幻觉**（独立 backlog 项，已有）：RMS 阈值 / VAD / 静音过滤，按下热键后录的音频如果整段 RMS < 阈值，跳过 STT 直接提示"未录到音频"。

4. **错误文案显示在 toast / 系统通知**：现在文案只在日志，用户体感弱。可以扩浮窗成 toast 形态（拉宽 + 文字），或者上 Linux `notify-send` / macOS `osascript display notification`。BACKLOG 现有"主动通知"那一条覆盖了类似方向。

5. **本轮 PR 后实测一阵看 pactl 200ms timeout 是否够**：如果不够，在 config 里暴露 `audio.pactl_timeout_ms` 让用户调。

## 工程数据

- 修改文件：8 个（recorder.py / `__main__.py` / overlay_linux.py / overlay_macos.py / locales × 3 / PLAN.md）
- 新增测试文件：1 个（test_recorder_probe.py，18 个用例）
- 扩展测试文件：2 个（+21 个用例）
- 测试结果：290 个用例全过（含 STT 之外的全套），ruff 全过
- recorder.py 覆盖率 60% → 97%
- 工作量：约 1 个开发日，其中 ~30% 花在"发现 query_devices 在 PipeWire 上不工作 → 调研 → 重写为 pactl 方案"
