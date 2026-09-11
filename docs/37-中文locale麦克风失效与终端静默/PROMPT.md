# 需求：中文 locale 下麦克风探测失效 + 终端零输出

## 背景

在一台**系统界面为简体中文**的 Ubuntu 机器（`mypc`，Ubuntu 24.04 / GNOME / X11 /
PipeWire，`LANG=zh_CN.UTF-8`）上安装 daobidao 1.0.5 后，暴露两个此前从未出现过的
问题。此前所有开发与使用机器都是英文界面，所以两个 bug 都一直潜伏着。

### Bug 1（P0）：中文 locale 下麦克风永远探测不到，完全无法录音

现象：麦克风（BOYA mini 2 USB）已插好、系统识别正常、`pactl` 里是默认输入源、
未静音、音量 100%，但按住热键无任何反应。日志里每按一次就是一条：

```
event='mic_offline' reason='probe_failed'
detail='no available input port (jack-detect: not available)'
```

即 `AudioRecorder.probe()` 在真正开录之前就把请求拦掉了，录音链路一次都没走到。

根因：`src/daobidao/recorder.py` 的 `_check_pactl_input_available()` 逐行解析
`pactl list sources` 的**英文字段名**：

- `recorder.py:118` — `stripped.startswith("Name: ")`
- `recorder.py:125` — `stripped == "Ports:"`

而 pactl 的输出**是本地化的**。同一台机器、同一条命令，只换 locale：

```
LC_ALL=C:            Name: alsa_input.usb-...BOYA_mini_2-02.analog-stereo
                     Ports:
                       analog-input-mic: 话筒 (type: Mic, priority: 8700, availability unknown)

zh_CN.UTF-8:         名称：alsa_input.usb-...BOYA_mini_2-02.analog-stereo
                     端口：
                       analog-input-mic: 话筒 (type: Mic, priority: 8700, availability unknown)
```

`名称：` / `端口：`（中文全角冒号、无空格）一条都匹配不上 → `found_input` 恒为
False → 函数返回 False → probe 抛 `MicUnavailableError("probe_failed", ...)`。

注意端口行里的 `availability unknown` **本身没有被翻译**，所以只要字段名对得上，
判定逻辑就是对的 —— 坏的只是字段名匹配这一层。

实机验证（mypc，同一个解释器、同一份代码，只换环境变量）：

```
LANG=zh_CN.UTF-8 → _check_pactl_input_available() = False   probe() FAIL
LC_ALL=C         → _check_pactl_input_available() = True    probe() OK
LC_ALL=C.UTF-8   → _check_pactl_input_available() = True    probe() OK
```

硬件与采集链路本身完全正常（已排除）：

- `parecord` 录 6 s：peak=389，非全零
- daobidao 自己那套 sounddevice（默认设备 `default` → PipeWire）录 6 s：
  peak=181，96000 个样本里 94957 个非零

现有测试为什么没抓到：`tests/test_recorder_probe.py` 把 `subprocess.run` 打桩，
喂的固定是**英文** fixture，locale 这个变量从来没进过测试。正好撞在全局
`CLAUDE.md`「环境是被测行为的输入，不是测试环境的属性」那一条上。

### Bug 2（P1）：终端零输出，启动看起来像卡死在模型下载

现象：终端里跑 `daobidao`，从头到尾只有 modelscope 自己打的一行：

```
INFO | modelscope_hub.download | Downloading 9 files from zengshuishui/Qwen3-ASR-onnx@master
```

之后**再无任何输出** —— banner、「预加载 STT 模型…」、「就绪！按住热键开始说话」
一条都不打。首次安装那次真下载耗时 415 s，期间终端就停在那一行不动，看起来完全
就是「卡在模型下载不结束」。实际上程序一直正常，日志文件里 ready 得好好的。

根因：commit `25f8087`（「默认 terminal log 静默」）把 stderr handler 改成只有
`--verbose` 才挂（`__main__.py:867-871`），而 `__main__.py` 里没有任何 `print()`，
于是应用自身的输出全部只进文件日志。那次改动的本意是「终端不要持续刷 INFO log」，
本身合理；问题是**把用户必须看到的少数几条启动里程碑一起静默了**，且它想消掉的
modelscope 那行其实是走 modelscope 自己的 logger handler，`redirect_stdout` 也没
拦住，最后剩下的唯一一行恰恰是最容易被误解成「卡住」的那行。

## 需求

1. **修 Bug 1**：让麦克风探测不再受用户 locale 影响 —— 解析机器输出时必须锁定
   一个确定的 locale，而不是跟着用户桌面语言走。
2. **修 Bug 2**：恢复终端上必要的启动反馈，但**不能退回**到 `25f8087` 之前那种
   持续刷结构化 INFO log 的状态 —— 那次静默的动机是成立的。
3. 两个 bug 都要有能在**任何 locale / 任何机器**上跑出逐字相同结论的测试；
   Bug 1 的测试必须把 locale 显式建模为被测行为的输入。

## 范围与约束

- 只改 Linux 侧的 pactl 探测与终端输出通道，不动录音采集、STT、热键逻辑。
- 不引入新依赖。
- 不改 `pactl` 之外的 probe 权威性设计（Linux 上 pactl 仍是唯一权威，
  见 `docs/32-录音麦克风离线检测/`）。
- 终端输出的具体文案走现有 i18n（`assets/locales/*.json`），不硬编码中文。

## 待确认

- 终端输出恢复到什么粒度（只要启动里程碑，还是连录音/识别结果也打）——
  在 `PLAN.md` 里给方案，由人类拍板。
