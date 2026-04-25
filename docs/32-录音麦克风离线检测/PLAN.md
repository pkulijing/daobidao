# 第 32 轮开发计划：录音时实时检测麦克风离线

## 0. 本轮范围

本轮要解决「按下热键说话 → 程序照常录空白 → 走 STT → paste 出空字符串或幻觉 token」的痛点，分两档同时落地：

1. **probe 档**：按下热键、即将进 `sd.InputStream` 之前，在 worker 线程里快速 `query_devices()` 校验默认输入设备还在
2. **中途断开监控档**：录音中如果设备消失（callback `status` flag 含 device-lost 类标志、或 stream 抛 `PortAudioError`），立即终止录音 + 提示用户

提示通道**只做浮窗错误状态**（红色药丸 + 麦克风带斜线 + 文案），不做系统通知。错误浮窗 2.5s 后自动 hide。5s 内只提示一次（去抖）。

**不在本轮 scope**：

- 空白音频幻觉（用户没说话时 STT 仍幻觉「嗯」「谢谢观看」）—— 单独 backlog（RMS 阈值 / VAD / 静音过滤）
- macOS `AVAudioSession` / Linux PipeWire device-change 事件订阅 —— 跟「`sounddevice` 一把梭」风格冲突
- 系统通知（`osascript display notification` / `notify-send`）—— 用户已确认只做浮窗

## 1. 整体方案概览

数据流（按下 → 录音中 → 松开）：

```
on_key_press (system thread)
   │ enqueue _do_key_press
   ▼
worker thread (daobidao-event-worker)
   │
   ├── 1. AudioRecorder.probe()       ← 新增。失败 → MicUnavailableError
   │      - sd.query_devices(kind='input')
   │      - 跑在 worker 线程；超时 200ms 兜底
   │      ↓ 失败
   │      _show_mic_offline_warning(reason="probe_failed")
   │      不进入录音状态，return
   │
   ├── 2. recorder.start[_streaming]() ← 包 try / except sd.PortAudioError
   │      - InputStream 构造或 .start() 抛错 → MicUnavailableError(reason="stream_error")
   │      - 同样走 _show_mic_offline_warning
   │
   ├── 3. _audio_callback (PortAudio thread)
   │      - 检测 status flag → 含 input_overflow & 持续 / 任何抢占性 flag
   │        → 通过 threading.Event 标记 device_lost；不在 callback 里做 stop
   │      - 在 callback 里只做 lightweight：set event + enqueue 到 worker
   │
   ├── 4. worker 收到 device_lost 事件
   │      - 调 _stop_stream_with_timeout(0.5s) 强行停 stream
   │      - _show_mic_offline_warning(reason="device_lost")
   │      - 清 _stream_state / _processing / accumulator
   │      - 浮窗错误态 2.5s 后自动 hide（覆盖正常的 ready 状态）
   │
   └── on_key_release: 如果已经因 device_lost 被终止，直接 return（早退）
```

去抖：`WhisperInput._last_mic_warning_at` 时间戳，5s 内同一 reason 不再弹（不同 reason 也合并去抖，避免 probe + device_lost 紧接着各弹一次）。

## 2. 新组件 / 修改清单

### 2.1 新增 `src/daobidao/recorder.py` 的内容

- 新增 `MicUnavailableError(Exception)`，字段：
  - `reason: Literal["probe_failed", "device_lost", "stream_error"]`
  - `detail: str | None`（原始异常 repr 或 status flag 字符串，用于日志）
- 新增 `AudioRecorder.probe(timeout: float = 0.2) -> None`
  - 在调用线程同步跑 `sd.query_devices(kind='input')`，但用 threading + 超时兜底
  - 任何失败 / 超时 / 返回结果不合法 → `raise MicUnavailableError(reason="probe_failed", detail=...)`
- 新增 `AudioRecorder.set_stream_status_callback(cb: Callable[[str], None])`
  - 用于把 callback 里识别出的 device-lost 信号 enqueue 给 WhisperInput；recorder 不直接拿 worker queue 句柄，保持解耦
- 修改 `_audio_callback`
  - 当 `status` 含设备消失类 flag（见 §5）时，调一次 `self._stream_status_cb(str(status))`
  - **不**在 callback 里 stop stream
  - 已有的 `if status: logger.warning(...)` 升级到 escalate 路径，但只在「识别为设备消失」时调 cb；普通 underflow / overflow 单次出现仍走 logger.warning（避免误报）
- 修改 `start()` / `start_streaming()`
  - 用 try/except 包 `sd.InputStream(...)` 构造和 `.start()`：抓 `sd.PortAudioError` + `OSError` → 重置 `_recording=False` + `raise MicUnavailableError(reason="stream_error", detail=repr(exc))`
- 新增内部 helper `_stop_stream_with_timeout(timeout: float = 0.5) -> bool`
  - daemon 线程跑 `self._stream.stop(); self._stream.close()`，主线程 `Event.wait(timeout)`
  - 超时返回 False，调用方丢弃 stream（不再 close 防止 hang，参考 24 轮 `terminate_portaudio` 模式）
  - `stop()` / `stop_streaming()` 内部改用这个 helper

### 2.2 修改 `src/daobidao/__main__.py`

- `WhisperInput.__init__` 新增字段：
  - `self._last_mic_warning_at: float = 0.0`
  - `self._mic_warning_cooldown_s: float = 5.0`
  - `self._mic_offline_during_recording: bool = False`（一次 session flag，press 时清零）
- `_do_key_press` 第一步加 probe：
  ```python
  try:
      self.recorder.probe()
  except MicUnavailableError as exc:
      self._show_mic_offline_warning(exc.reason, exc.detail)
      return
  ```
  紧接着原本的 `recorder.start[_streaming]()` 也包 try/except `MicUnavailableError`，捕获后清掉刚 set 的浮窗 / `_stream_state`，return
- `_do_key_release` 顶部加 short-circuit：
  ```python
  if self._mic_offline_during_recording:
      self._mic_offline_during_recording = False
      return  # 录音根本没成功开起来 / 中途断开
  ```
- 新增 `_show_mic_offline_warning(reason: str, detail: str | None)`
  - 去抖：`time.monotonic() - self._last_mic_warning_at < 5.0` → 直接 return
  - 否则更新时间戳，写日志（`logger.warning("mic_offline", reason=..., detail=...)`），调 `self._overlay.show_error(t("main.mic_offline_title") + " · " + t("main.mic_offline_hint_settings"))`
  - 同时确保 `_processing=False`、`_stream_state=None`、accumulator 清空（避免后续 release 卡死）
- 新增回调 `_on_stream_status_signal(status_flag: str)`
  - 由 recorder 在 PortAudio 线程里调；本函数 enqueue 一个 `_handle_device_lost(status_flag)` 到 `_event_queue`，**不在 PortAudio 线程做实质工作**
- 新增 `_handle_device_lost(status_flag: str)` —— worker 线程里跑
  - 设 `_mic_offline_during_recording = True`
  - 调 recorder 的 `_stop_stream_with_timeout()`（单次）
  - 调 `_show_mic_offline_warning("device_lost", status_flag)`
  - 流式路径下还要清 `_stream_state` / accumulator / `_processing`
- 在 `__init__` 里 `self.recorder.set_stream_status_callback(self._on_stream_status_signal)`
- `_notify_status` 不变（保持现有三态），新增的「错误态」不走 status，直接调 `overlay.show_error()`，避免跟 ready 状态机混淆

### 2.3 修改 `src/daobidao/backends/overlay_linux.py`

- 新增 `RecordingOverlay.show_error(message: str)`
  - 通过 `GLib.idle_add` 调 `_do_show_error`，画法见 §8
  - 启一个 `GLib.timeout_add(2500, self._do_hide)` 让错误浮窗 2.5s 后自动 hide
  - 错误状态时 `_audio_callback.set_level()` 不应再驱动跳动条 → 用一个内部 flag `_in_error_state` 拦截 `set_level`（在错误窗口的 2.5s 内忽略）

### 2.4 修改 `src/daobidao/backends/overlay_macos.py`

- 镜像同样新增 `show_error(message: str)`，画法走 `_perform_on_main` + `NSTimer` 延迟 hide

### 2.5 修改 `src/daobidao/assets/locales/{zh,en,fr}.json`

新增 3 个 key（详见 §9）。

### 2.6 新增 / 扩展测试

- 新增 `tests/test_recorder_probe.py`
- 扩展 `tests/test_recorder_streaming.py`（status flag 路径）
- 扩展 `tests/test_main_worker.py` / `tests/test_main_streaming.py`（probe 失败 / 中途断开 / 去抖）

## 3. 错误类型设计

文件位置：`src/daobidao/recorder.py` 顶部定义。

```python
class MicUnavailableError(RuntimeError):
    """录音前 probe / 启动 stream / 中途监控发现麦克风不可用。

    reason 取值（开发者枚举，不进 i18n）：
        - "probe_failed":  query_devices 报错 / 默认 input 不存在 / 超时
        - "stream_error":  sd.InputStream 构造或 start() 抛 PortAudioError / OSError
        - "device_lost":   录音中 callback status 含设备消失类 flag
                          （这条主要用于日志归因，构造时调用方传入）
    """
    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)
```

放 `recorder.py` 而不是单独 errors 文件：异常跟 `AudioRecorder` 强耦合，外部除了 `WhisperInput` 不会 import；项目目前也没有统一的 errors 模块。

## 4. probe 实现细节

> ⚠️ **方案在实现期间发生了大幅修订**。原计划"`sd.query_devices(kind='input')` 一把抓"在 Linux + PipeWire 实测时被发现完全不可靠 —— PipeWire 在物理麦拔了之后仍会暴露虚拟 default 设备，sounddevice 这一层骗不过去。下面是**修订后**的最终方案；原方案保留在 §4.4 作为决策记录。

### 4.1 Linux：`pactl list sources` 唯一权威

PipeWire / PulseAudio 在 `pactl list sources` 的 `Ports:` 字段把 ALSA HDA codec 的 jack-detect 状态暴露成 `available` / `not available` / `availability unknown`。Chrome 的 `getUserMedia` 也是看这一信号判定"未检测到麦克风"。

实测拔 USB 麦后：

```
Source #50
    Name: alsa_input.pci-0000_00_1f.3.analog-stereo
    Ports:
        analog-input-front-mic: Front Microphone (..., not available)
        analog-input-rear-mic: Rear Microphone (..., not available)
        analog-input-linein: Line In (..., not available)
```

而 `sd.query_devices(kind='input')` 此时仍返回 `{name: 'default', max_input_channels: 64}`，`InputStream` 也照开，callback 干净不带 status flag，**只发全 0 静音流**——sounddevice 整条链全部失效。

`pulseaudio-utils`（包含 `pactl`）是 Linux 安装路径的系统依赖，`setup.sh` / `install.sh` 的 `APT_PKGS` 已经包含。pactl 不可用 → `MicUnavailableError("probe_failed", detail="pactl unavailable; install pulseaudio-utils...")`，提示用户安装。

```python
class PactlUnavailableError(RuntimeError):
    """pactl 命令不可用（没装 / 调用失败 / 输出无法解析）。"""


def _check_pactl_input_available() -> bool:
    """返回 True/False 表示有/无可用 input；pactl 不可用时抛 PactlUnavailableError。"""
    proc = subprocess.run(
        ["pactl", "list", "sources"],
        capture_output=True, text=True, timeout=0.5,
    )  # 超时 / FileNotFoundError → PactlUnavailableError
    # 解析 Ports: 字段
    # - 只看 alsa_input.* 且非 .monitor
    # - port 末尾括号里的 availability 字段：
    #   "available" / "availability unknown" → 算可用
    #   "not available" → 算不可用
    return found_input and found_available


def probe(self, timeout: float = 0.2) -> None:
    if sys.platform.startswith("linux"):
        try:
            available = _check_pactl_input_available()
        except PactlUnavailableError as exc:
            raise MicUnavailableError(
                "probe_failed",
                f"pactl unavailable (need pulseaudio-utils): {exc}",
            ) from exc
        if not available:
            raise MicUnavailableError(
                "probe_failed",
                "no available input port (jack-detect: not available)",
            )
        return
    # 非 Linux：走 query_devices（见 §4.2）
    ...
```

### 4.2 macOS / 其他：`query_devices` 兜底（主流场景可靠）

CoreAudio 在 macOS 上**主流场景**是直接反映硬件的（MacBook 内置麦永远在，拔蓝牙 / USB 麦时 CoreAudio 自动 fallback 到内置），`sd.query_devices(kind='input')` 在所有 input 都消失时会抛 `PortAudioError("No default input device available")`。

但**极端场景**（Mac mini / Mac Pro 等无内置麦的桌面机 + 外接 USB 麦拔掉）CoreAudio 可能返回 `CADefaultDeviceAggregate-xxxx-x` 占位设备，跟 PipeWire 一样有"虚拟 default 欺骗"问题。本轮**不解决**这条边角场景，留 follow-up：用 `system_profiler SPAudioDataType` 或 `pyobjc-framework-CoreAudio` 调 `AudioObjectGetPropertyData(kAudioHardwarePropertyDevices)`。详见 §12.5 + BACKLOG 新条目。

`query_devices` 在某些 Linux 配置下冷启动可能阻塞几十 ms，所以包了个 daemon 线程 + Event 套 200ms timeout 兜底。这条对 macOS 也保留（开销几乎为 0）。

### 4.3 为什么要在 worker 线程里跑

`_do_key_press` 跑在 `daobidao-event-worker` 里，probe 自然落在 worker 上，**不需要额外架构** —— 绝不能让 pactl subprocess 的 ~50ms 阻塞热键回调线程（22 轮死锁教训）。

### 4.4 修订前（已弃用）的 query_devices 方案

原计划：Linux 也走 `sd.query_devices(kind='input')`，200ms timeout 兜底，看 `max_input_channels > 0` 判定可用。

为什么弃用：实测发现 PipeWire 在物理麦拔了之后仍会通过 sounddevice 暴露 `default` 虚拟设备，`max_input_channels=64`，`InputStream.start()` 也成功，callback 60 次都收到，音频全 0，无 status flag。这条路完全失效。

教训：**接 Linux 音频栈时不能只看 `sd.query_devices` / PortAudio 这一层，必须从 PipeWire / PulseAudio 元数据层（pactl）看 jack-detect 状态**。Chrome 的 `getUserMedia` 也是走这个路径。

## 5. callback status 监控细节

### 5.1 哪些 CallbackFlags 算「设备消失」

PortAudio `CallbackFlags` 是 bitmask，sounddevice 暴露成 `sd.CallbackFlags` 对象，`str()` 出来是 `"input overflow"` / `"input underflow"` / `"priming output"` 这种空格分隔的字符串。

跨平台经验值（来自 sounddevice issue 跟踪 + PortAudio 源码）：

| flag | Linux (ALSA/PulseAudio) | macOS (CoreAudio) | 是否设备消失 |
|---|---|---|---|
| `input_underflow` | 偶发，buffer 没及时填 | 极少见 | 否（性能问题，不该升级）|
| `input_overflow` | 偶发，应用没及时 read | 偶发 | 大多数情况是 overload，**不**直接判设备消失 |
| `priming_output` | 不适用 | 不适用 | 否 |

**关键事实**：`sounddevice` 的 callback status flag 在「USB 麦拔出 / 蓝牙断开」时各平台行为不一致：

- Linux ALSA：往往是 `read()` 直接抛 `PortAudioError` —— callback 收到 `input_overflow` 几次后 stream 进入 aborted 状态
- macOS CoreAudio：拔耳机的瞬间 callback 可能完全停掉（不再被调用）；route 切换走的是 AVAudioSession 通知，sounddevice 抓不到

所以**单纯靠 callback status flag 不可靠**。本轮的策略：

1. **持续性 input_overflow 检测**：在 callback 里维护一个 `_consecutive_overflow_count`，连续 ≥ 5 次 callback 都带 input_overflow → 升级为 device_lost。单次 overflow 不报。阈值 5 是经验：正常 overload 顶多连续 1-2 次就恢复
2. **InputStream 抛错被动捕获**：`recorder.start()` / `start_streaming()` 已经包 try/except `PortAudioError`；进一步在 callback 外层（PortAudio 自己会 invoke callback，没法 try/except 包它），但 `stream.stop()` / `stream.close()` 路径加 try/except，遇错也走 device_lost
3. **stream 状态轮询兜底**（**可选**，本轮不做）：起一个 daemon 监控线程每 500ms 看一眼 `self._stream.active`，从 True 翻 False 但 recorder 仍标 `_recording=True` → 判 device_lost

**本轮选 1 + 2**，不引入轮询线程。这意味着 macOS 上某些「callback 直接停掉」的场景仍然抓不住，但能兜住下一次按键（按 5 之前 probe 会失败）；Linux ALSA 路径上 1 + 2 应该足够。**风险与降级在 §12 详述**。

### 5.2 怎么从 PortAudio callback 把事件送回 worker

- `AudioRecorder.set_stream_status_callback(cb)`：cb 由 `WhisperInput` 提供，**只做 lightweight 的 enqueue**
- callback 内部识别「设备消失」信号后调 `self._stream_status_cb(status_str)`（非 None 时）
- `WhisperInput._on_stream_status_signal(status_str)` 实现就是 `self._event_queue.put(lambda: self._handle_device_lost(status_str))`
- 真正调 `_stop_stream_with_timeout()`、清状态、show error 浮窗都在 worker 线程做

**绝不**在 PortAudio 回调线程里调 `stream.stop()`：CoreAudio HAL mutex 顺序反向问题，22 / 24 轮已被坑过两次。

### 5.3 callback 自己置 flag 不 stop stream

callback 只 set flag + 通知 worker，**不**调 `self._stream.stop()`。原因：

- callback 跑在 PortAudio 内部 IO 线程，stop 自己等同自死锁
- 让 worker 来 stop：worker 的 stop 路径已有 `_stop_stream_with_timeout` 兜底
- 若 stop 超时（PortAudio 真死锁了），丢弃 stream 引用，下一次 press 时新建一个

## 6. stop with timeout 兜底

`AudioRecorder._stop_stream_with_timeout(timeout=0.5)`：

```python
def _stop_stream_with_timeout(self, timeout: float = 0.5) -> bool:
    if self._stream is None:
        return True
    stream = self._stream
    self._stream = None  # 先解引用，避免 stop 卡住后下次 press 再用同一个
    done = threading.Event()
    err: list = []

    def _run():
        try:
            stream.stop()
            stream.close()
        except BaseException as exc:
            err.append(exc)
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True, name="recorder-stop").start()
    if not done.wait(timeout):
        logger.warning("stream_stop_timeout", timeout=timeout)
        return False  # stream 引用已被丢弃，不强 close（参考 24 轮 _terminate 兜底）
    if err:
        logger.warning("stream_stop_error", error=repr(err[0]))
        return False
    return True
```

`stop()` / `stop_streaming()` 内部改用这个 helper，正常情况下 0.5s 足够，超时只 log + 丢弃。

## 7. 去抖

`WhisperInput` 字段：

```python
self._last_mic_warning_at: float = 0.0  # time.monotonic()
self._mic_warning_cooldown_s: float = 5.0
```

`_show_mic_offline_warning` 顶部：

```python
now = time.monotonic()
if now - self._last_mic_warning_at < self._mic_warning_cooldown_s:
    logger.debug("mic_offline_warning_suppressed", reason=reason)
    return
self._last_mic_warning_at = now
```

不分 reason 合并去抖：probe 失败后用户通常立刻松手再试 → 5s 内 device_lost 信号也不弹（避免双重打扰）。

## 8. 浮窗错误态画法

### 8.1 视觉

- 药丸背景：红色 `#DC2626`（Tailwind red-600，与现有蓝色 `#1E3A8A` 视觉一致的饱和度）
- 麦克风图标：复用现有矢量画法（话筒头 + U 托架 + 连接杆 + 底座）
- 在麦克风之上画一道**白色对角线**（左下 → 右上，line_width=2，line_cap=ROUND），表示禁止 / 离线
- 跳动长条不画（错误态没有 RMS）
- 文案不画在浮窗里（120×34 太挤）—— 走 logger + 后续可考虑 tooltip。**本轮浮窗只用颜色 + 斜线表达**；文案传给 `show_error(message)` 会落在日志里供用户调试时看，并通过 i18n 单元测试用例锁定

### 8.2 API

```python
def show_error(self, message: str) -> None:
    """显示麦克风离线错误状态浮窗，2.5s 后自动 hide。

    message 当前不渲染到浮窗内（药丸太窄），落到 stdout / 日志做问题排查。
    visual: 红色药丸 + 麦克风图标加白色斜线。
    """
```

### 8.3 自动 hide

- Linux: `GLib.timeout_add(2500, self._do_hide)`
- macOS: `NSTimer.scheduledTimerWithTimeInterval_invocation_repeats_(2.5, ...)` 或 `dispatch_after`，调 `_perform_on_main(self._do_hide)`

### 8.4 set_level 抑制

错误态期间 `set_level()` 来的 RMS 应被忽略（按理录音都已停了不会再来，但流式路径下 callback enqueue 跟错误态切换之间有 race）：用 `_in_error_state` flag，2.5s 自动 hide 时翻回 False。

### 8.5 不动 `update(text)`

现有 `update(text)` 实际只 fade out 跳动条（28 轮在 processing 阶段用），保持原行为不动。`show_error` 是独立的新方法。

## 9. i18n 新 key

`zh.json`：
```json
"main.mic_offline_title": "麦克风离线",
"main.mic_offline_hint_settings": "在设置页「麦克风检测」确认设备",
"main.mic_lost_during_recording": "录音中断：麦克风离线"
```

`en.json`：
```json
"main.mic_offline_title": "Microphone offline",
"main.mic_offline_hint_settings": "Open Settings → Microphone Check to verify",
"main.mic_lost_during_recording": "Recording aborted: microphone offline"
```

`fr.json`：
```json
"main.mic_offline_title": "Microphone hors ligne",
"main.mic_offline_hint_settings": "Vérifiez via Paramètres → Test du microphone",
"main.mic_lost_during_recording": "Enregistrement interrompu : microphone hors ligne"
```

文案串联 23 轮：`mic_offline_hint_settings` 明确指引用户去设置页诊断，把两轮工作连起来。

## 10. 测试计划

### 10.1 新增 `tests/test_recorder_probe.py`

mock recorder 模块的 `sd`：

- `test_probe_succeeds_on_normal_device`: query_devices 返回 `{"max_input_channels": 1, "name": "..."}` → probe 不抛
- `test_probe_raises_when_query_devices_throws`: query_devices 抛 PortAudioError → MicUnavailableError(reason="probe_failed")
- `test_probe_raises_when_zero_input_channels`: 返回 `{"max_input_channels": 0}` → MicUnavailableError
- `test_probe_raises_on_timeout`: query_devices 故意 sleep 1s，probe(timeout=0.05) → MicUnavailableError(detail 里含 "timeout")

### 10.2 扩展 `tests/test_recorder_streaming.py`

- `test_callback_status_overflow_single_does_not_escalate`: fake fire 一次带 status="input overflow" → recorder 不调 status callback
- `test_callback_status_overflow_persistent_escalates`: 连续 5 次 fire 带 "input overflow" → recorder 调 status callback 一次
- `test_start_raises_mic_unavailable_when_input_stream_throws`: monkeypatch fake_sd.InputStream 构造抛 PortAudioError → recorder.start() raise MicUnavailableError(reason="stream_error")
- `test_stop_stream_with_timeout_succeeds_normally`: FakeInputStream.stop 正常返回 → helper 返回 True
- `test_stop_stream_with_timeout_returns_false_on_hang`: FakeInputStream.stop 故意 sleep > timeout → helper 返回 False，且 self._stream 已被置 None

### 10.3 扩展 `tests/test_main_worker.py`

新增 fixture：`recorder.probe` 和 `recorder.start` 都可控制。

- `test_probe_failure_skips_recording`: probe 抛 MicUnavailableError → on_key_press → worker 处理后 recorder.start 不被调；overlay.show_error 被调
- `test_probe_failure_release_is_noop`: 紧接着 on_key_release → recorder.stop 不被调，无异常
- `test_mic_warning_debounced_within_5s`: 连续两次 probe 失败 → overlay.show_error 只被调一次
- `test_mic_warning_resets_processing_flag`: probe 失败时 `_processing` 不被卡 True
- `test_device_lost_during_recording_aborts_session`: 模拟录音中触发 `_handle_device_lost` → recorder.stop_streaming 被调（或 stream 被强 stop），show_error 被调，`_stream_state` 清零

### 10.4 扩展 `tests/test_main_streaming.py`

- `test_stream_status_signal_enqueued_to_worker`: 通过 fake recorder 触发 status cb → 验证 `_event_queue` 收到一个 `_handle_device_lost` 任务
- `test_device_lost_during_streaming_clears_state`: 流式路径下走完 device_lost → `_stream_state is None`、`_processing is False`、accumulator 空

### 10.5 不写

- 真 sounddevice 集成测试（CI 拔不了麦克风）
- 浮窗 GTK / Cocoa show_error 单测（保持现有「不测 overlay」惯例，靠手动验证）
- 文案具体内容断言（i18n 文件 lint 已经覆盖 key 是否存在）

## 11. 手动验证步骤

### 11.1 Linux

1. 起 `uv run daobidao`，按热键正常录音，确认浮窗蓝色出现 → ready 路径无 regression
2. 拔 USB 麦 / `pactl set-default-source` 改一个不存在的 source，按热键 → 浮窗短暂显示红色 + 斜线 2.5s → 松手不 paste → 日志含 `mic_offline reason=probe_failed`
3. 按住录音中拔 USB 麦 → 期望：~1s 内浮窗变红 + 斜线，松手不 paste → 日志含 `mic_offline reason=device_lost` + `stream_status` 中含 input overflow 多次记录
4. 5s 内连续按热键 5 次（麦克风仍离线）→ 浮窗只首次变红，后续被去抖；日志含 `mic_offline_warning_suppressed`
5. 重新插回 USB 麦，5s 之后再按热键 → 正常录音，提示通道恢复

### 11.2 macOS

1. 起 `uv run daobidao`（或 .app bundle），同上 step 1 基线
2. 关蓝牙耳机 / System Settings → Sound → Input 改成「No input devices available」状态，按热键 → 红色浮窗（注：macOS 上可能 query_devices 仍返回内置 mic，需要实测确认是不是 macOS 哪怕没默认 input 也会列出 placeholder；如果是，调整 probe 校验为「default input != -1」）
3. 按住录音中关蓝牙耳机 → 这里是已知风险点：CoreAudio route 切换走 AVAudioSession 通知，callback 可能停止被调而不报 status flag → §12 风险讨论
4. 同上 step 4 / 5 验证去抖

### 11.3 退出路径回归

- 任意 reason 触发错误浮窗后立刻 Ctrl+C → 进程能在 2s 内退出，不卡 24 轮的 `terminate_portaudio` 路径

## 12. 风险与 fallback

### 12.1 callback status 在某平台不报「设备消失」

**风险**：macOS CoreAudio 在 route 切换瞬间往往直接停掉 callback（不带 status flag），仅靠 callback 监控的中途断开档抓不住。

**降级**：probe 路径仍兜底 —— 用户下次按热键时 probe 必会抓到（query_devices 在没有可用 input 时直接抛）。最坏情况 = 用户这次说话录了空白，再按一次时被告警。这不会引起回归（当前行为本来就是空白录音），只是体验稍差。

**后续**：如果实测发现 macOS 上中途断开档完全失效，可考虑加一个 daemon 监控线程轮询 `self._stream.active`（500ms 周期），翻 False 时升级为 device_lost。本轮**先不做**，避免增加线程数 + 复杂度，看实际反馈再加。

### 12.2 `_stop_stream_with_timeout` 超时时 stream 句柄泄漏

**风险**：超时分支下我们把 `self._stream = None` 但底层 PortAudio stream 仍存活，等于资源泄漏。

**降级**：daemon 线程会一直等 PortAudio 自己回来；进程退出时 `terminate_portaudio()`（24 轮）会把整个 PortAudio 实例终结，所以**只是单次 session 内泄漏**，不会持续累积。代价是再开一个新 stream 时可能 PortAudio 内部状态不一致 → 实测如果观察到，下一轮再处理。

### 12.3 probe 200ms 超时在某些 Linux 配置下被频繁触发

**风险**：BACKLOG 提到「冷启动 PulseAudio query 几十 ms」，极端情况下某些 Linux 桌面（KDE + Wireplumber 老版本）实测可能 > 200ms。

**降级**：可以把 timeout 提到 500ms 作为 config 项（`audio.probe_timeout_ms`），但本轮**不做**配置项 —— 默认 200ms 是合理起点，先实测再决定要不要可调。

### 12.4 错误浮窗 2.5s 自动 hide 跟正常 ready 状态机的 race

**风险**：错误浮窗显示中，用户重新按热键开始正常录音 → ready 路径会 `overlay.show()`，但 2.5s 计时器还在跑，可能在录音中途 hide。

**缓解**：`show_error` 的 timeout 实现里在 hide 前检查 `_in_error_state` flag，若已被新 show() 重置则不 hide。Linux 用 `GLib.timeout_add` 返回值 + 在 show() 时 `GLib.source_remove`；macOS 用 `NSTimer.invalidate`。

### 12.5 macOS 边角场景：无内置麦的桌面机拔 USB 麦

**风险**：本轮 macOS 仍走 `sd.query_devices`，对 MacBook 主流场景（内置麦永远在）可靠；但 Mac mini / Mac Pro 等**无内置麦**的桌面机用户拔掉外接 USB 麦时，CoreAudio 可能返回 `CADefaultDeviceAggregate-xxxx-x` 占位设备，`query_devices` 看到 `max_input_channels > 0`，probe 通过 → 跟 Linux PipeWire 同样的虚拟 default 欺骗。

**降级**：本轮**不修**。理由：

- 没有 Mac 测试机，盲改 macOS 风险大
- MacBook 用户（绝大多数）不受影响
- 真受影响的用户会发现"按热键没反应"，可以提 issue 后单开一轮处理

**follow-up 方向**（择一）：

- `system_profiler SPAudioDataType`：Mac 自带 shell 工具，类似 pactl 的位置，最对称的实现
- `pyobjc-framework-CoreAudio` 调 `AudioObjectGetPropertyData(kAudioHardwarePropertyDevices)`：原生最准，但要加依赖
- `AudioObjectAddPropertyListener(kAudioHardwarePropertyDefaultInputDevice)`：能做"中途断开监控"，工程量最大

BACKLOG 已加对应条目。

### 12.6 中途断开档在 Linux + PipeWire 上失效

**风险**：原计划 callback 内连续 5 次 `input_overflow` 升级 device_lost，实测 PipeWire 在物理麦拔了之后给的是**完美静音流** —— callback 干净不带任何 status flag，这条监控完全抓不住。

**当前状态**：`_OVERFLOW_DEVICE_LOST_THRESHOLD` + `_maybe_signal_device_lost` 代码保留，服务于：

- macOS 上某些 PortAudio 版本会在设备消失时报 `input_overflow`
- 纯 ALSA 系统（无 PipeWire / PulseAudio）路径
- 真 overload 场景的归因日志

但 Linux + PipeWire 主流场景下这条**不工作**。降级路径：用户下次按热键时 probe（pactl）会兜住。

**follow-up 方向**：在 Linux 上额外起一个 daemon 线程，每 ~500ms 调一次 pactl 看端口可用性，翻 false 时升级 device_lost。开销不大但要单测；本轮先观察实际反馈再决定。

## 13. scope 估算

行数估计：

| 文件 | 新增行 | 修改行 |
|---|---|---|
| `recorder.py` | ~80 | ~20 |
| `__main__.py` | ~60 | ~15 |
| `overlay_linux.py` | ~50 | ~5 |
| `overlay_macos.py` | ~55 | ~5 |
| `assets/locales/{zh,en,fr}.json` | 9 (3 × 3) | 0 |
| `tests/test_recorder_probe.py` | ~80 | 0 |
| `tests/test_recorder_streaming.py` | ~50 | 0 |
| `tests/test_main_worker.py` | ~80 | 0 |
| `tests/test_main_streaming.py` | ~30 | 0 |

**总计**：~500 行新增 + ~50 行修改，2 个平台 overlay 改动各一次实测。

工作量：约 1.5-2 个开发日（含 Linux + macOS 实测）。

follow-up：

- 如果 macOS 中途断开档真的失效（§12.1），单开一轮用 stream.active 轮询线程
- 如果 200ms probe timeout 不够（§12.3），加 config 项
- 空白音频幻觉是另一条单独 backlog（不在本轮）

## 14. 实现顺序建议

按依赖正向铺开，每步可独立 commit：

1. `recorder.py`：定义 `MicUnavailableError`、`probe()`、`_stop_stream_with_timeout()`、`set_stream_status_callback()`、`_audio_callback` 升级
2. `tests/test_recorder_probe.py` + `tests/test_recorder_streaming.py` 扩展，跑通 recorder 层
3. `__main__.py`：新增字段、`_show_mic_offline_warning`、`_on_stream_status_signal`、`_handle_device_lost`、`_do_key_press` / `_do_key_release` 接入
4. `tests/test_main_worker.py` + `tests/test_main_streaming.py` 扩展，跑通 main 层
5. `overlay_linux.py` + `overlay_macos.py`：新增 `show_error`，含自动 hide / set_level 抑制
6. `assets/locales/{zh,en,fr}.json` 新增 key
7. Linux 拔麦实测（步骤见 §11.1）
8. macOS 拔耳机实测（步骤见 §11.2）；按结果决定是否走 §12.1 fallback
