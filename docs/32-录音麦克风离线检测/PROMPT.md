# 第 32 轮开发需求：录音时实时检测麦克风离线

## 背景

23 轮在设置页加了**被动式**麦克风检测 —— 用户主动点「检测」才能看到设备列表、波形、回放，知道麦克风有没有、音质如何。

但**主流程录音时**还是机械执行：
- 用户在设置页能看到「麦克风没了」，可一旦按下热键说话，程序照常走录音 → STT → paste，最后 paste 出一段空字符串
- 更糟的是 Qwen3-ASR 对空白输入偶尔会幻觉出「嗯」「谢谢观看」之类的弱信号 token
- 用户得反复试才意识到是麦克风问题，体验很差，影响信任感（不知道是程序坏了还是自己机器坏了）

典型触发链：
- 蓝牙耳机休眠断连 / USB 麦拔出
- 某次系统更新后默认输入设备改了
- macOS 切换到外接显示器时音频路由 reset
- Linux PipeWire / PulseAudio 重启后 device index 变化

## 希望达到

1. **录音开始前**（按下热键瞬间）：快速校验当前默认输入设备还在 → 不在则**不进入录音状态**，浮窗（或托盘 / 通知）里提示「麦克风离线」，松开热键不 paste
2. **录音过程中**：如果设备中途断开（`sounddevice` 抛 `PortAudioError`，或 callback 的 `status` flag 带 input overflow / device unavailable），**立即终止录音**并以同样方式提示用户，不让 paste 继续走
3. 提示做成可关闭 / 不打扰（连续断连时不刷屏，最小提示间隔比如 5s）
4. **不引入新的运行时依赖** —— `sounddevice` 已经够用，不要扯 PipeWire / AVAudioSession 那一套

## 不在本轮 scope

- **空白音频幻觉**（用户没说话时 STT 仍幻觉）：另一个问题，单独 backlog（RMS 阈值 / VAD / 静音过滤），本轮不解决
- **macOS / PipeWire device-change 事件订阅**：更准但更重，要写两套平台代码，跟当前「`sounddevice` 一把梭」的简洁风格冲突，BACKLOG 里明确不优先

## 风险点 / 注意点（BACKLOG 里已经标过的）

- `sd.query_devices()` 在某些 Linux 配置下会阻塞几十 ms（冷启动 PulseAudio query），**不要在热键回调线程同步调** —— 22 轮专门修过热键回调死锁。probe 必须在 worker 线程跑
- 回调里 status flag 的语义跨平台不一致，macOS / Linux / 不同 PortAudio 版本对「设备消失」的报告方式可能不同，需要 Linux + macOS 各拔一次实测
- 「中途断开后 stop 流」在某些 PortAudio 版本里会 hang（类似 24 轮 CoreAudio 死锁），要带超时兜底
- 跟 23 轮设置页麦克风检测的关系：运行时检测到离线时，提示文案附一句「打开设置页检测麦克风」做引导，把两轮工作串起来

## 优先级

**中偏高** —— 用户主动报的痛点。建议先做 probe 那一档（最小可行），实测一段时间再决定要不要上中途断开监控。
