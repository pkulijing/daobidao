# 实现计划

## 一、先实证：几条外部行为断言

写设计之前先把涉及 `pactl` / modelscope 的断言在真机（mypc，Ubuntu 24.04 /
PipeWire / pactl 16.1 / `LANG=zh_CN.UTF-8`）上跑了一遍，结论直接决定选型。

### 1. pactl 输出确实随 locale 本地化，但可用性关键字没被翻译

```console
$ LC_ALL=C pactl list sources | sed -n '/BOYA_mini_2-02.analog-stereo$/,/^$/p' | grep -E "Ports:|analog-input|Active Port"
	Ports:
		analog-input-mic: 话筒 (type: Mic, priority: 8700, availability unknown)
	Active Port: analog-input-mic

$ LC_ALL=zh_CN.UTF-8 pactl list sources | sed -n '/BOYA_mini_2-02.analog-stereo$/,/^$/p' | grep -E "端口|analog-input"
	端口：
		analog-input-mic: 话筒 (type: Mic, priority: 8700, availability unknown)
	活动端口：analog-input-mic
```

被翻译的只有**字段名**（`Name:`→`名称：`、`Ports:`→`端口：`、`Active Port:`→
`活动端口：`）和 port 的 description（`Microphone`→`话筒`）；解析真正依赖的
`availability unknown` / `not available` **保持英文**。所以只要把字段名这层锁死，
现有解析逻辑就是对的，不需要重写判定。

### 2. `LC_ALL=C` 足以压制翻译，即使 `LANGUAGE=zh_CN:zh` 还在

上面那条 `LC_ALL=C` 是在 `LANGUAGE=zh_CN:zh` 的会话里跑的，输出仍是英文 —— 说明
`LC_ALL=C` 已经能覆盖 gettext 的 `LANGUAGE` 优先级。仍会同时置 `LANGUAGE=""` 做
双保险，成本为零。

用真代码验（同一解释器、同一份 1.0.5、只换环境变量）：

```
LANG=zh_CN.UTF-8 → _check_pactl_input_available() = False   probe() FAIL
LC_ALL=C         → _check_pactl_input_available() = True    probe() OK
LC_ALL=C.UTF-8   → _check_pactl_input_available() = True    probe() OK
```

### 3. 断言被推翻：`pactl --format=json` **不能**用来绕开 locale 问题

原本想过更稳的路子 —— 改用结构化输出，键名固定英文，从根上不受 locale 影响。
实测**不成立**：pactl 16.1 的 JSON writer 遇到本地化字符串直接吐错：

```console
$ LANG=zh_CN.UTF-8 pactl --format=json list sources | head
Invalid non-ASCII character: 0xffffffe6
Invalid non-ASCII character: 0xffffffe6
...
```

即 JSON 模式**同样要先锁 locale 才能用**，并没有省掉那一步；再加上
`--format=json` 要 PulseAudio ≥ 16.0（Ubuntu 20.04 的 pactl 13 没有），换过去
反而多一条版本依赖。**结论：维持文本解析，只加 locale 锁定。**

### 4. 首次下载时用户看到的是一个「9 个文件」的聚合进度条

缓存命中时终端只出现：

```
Downloading:   0%|          | 0/9 [00:00<?, ?file/s]Downloading: 100%|██████████| 9/9
```

modelscope 新版 `snapshot_download` 并行下载、只打这一个**按文件计数**的聚合条，
没有按字节的分文件条。真下载时 9 个文件里最大那个（`decoder.int8.onnx`，756 MB）
会让这个条在同一格上停数分钟不动 —— 这正是「看起来卡死」的直接来源，
光靠 tqdm 本身补不上反馈。

## 二、Bug 1：pactl 解析锁定 C locale

### 改动

`src/daobidao/recorder.py` 的 `_check_pactl_input_available()`，给
`subprocess.run` 显式传 env：

```python
env = {**os.environ, "LC_ALL": "C", "LANGUAGE": ""}
proc = subprocess.run(
    ["pactl", "list", "sources"],
    capture_output=True,
    text=True,
    timeout=_PACTL_TIMEOUT_S,
    env=env,
)
```

`env=` 是整体替换而非叠加，所以必须从 `os.environ` 拷一份基底（`XDG_RUNTIME_DIR`
/ `PULSE_SERVER` / `PATH` 都得留着，否则 pactl 连不上音频服务器）。

一并把函数 docstring 里补一句「本函数解析的是机器输出，必须在固定 locale 下取」，
把这个约束钉在代码里而不是只写在 docs。

### 测试（先红后绿）

`tests/test_recorder_probe.py` 新增 3 条，核心是**把 locale 建模成被测行为的输入**，
而不是跟着跑测机器的环境走：

1. `test_pactl_parser_immune_to_user_locale` —— 桩里放一个**会看 env 的假 pactl**：
   收到的 env 若把 locale 锁成 C 就返回英文 fixture，否则返回**真机抓来的中文
   fixture**；断言 `_check_pactl_input_available() is True`。
   这条在改动前必红（拿到中文输出 → False），改动后绿。
2. `test_pactl_run_forces_c_locale` —— 直接断言传给 `subprocess.run` 的 env 里
   `LC_ALL == "C"`、`LANGUAGE == ""`，且 `PATH` 等既有变量仍在（防止有人图省事写成
   `env={"LC_ALL": "C"}` 把 pactl 的运行环境掐了）。
3. `test_pactl_parser_returns_false_on_zh_output_without_locale_lock` —— 直接喂中文
   fixture 给解析器，断言它返回 False。这条**不是**要保留 bug，而是把「解析器只认
   英文」这个前提显式钉住：将来谁改动 locale 锁定，这条会提醒他解析器这边也得跟着改。

中文 fixture 用 mypc 上真抓的那段（含 `名称：` / `端口：` / `活动端口：` 与全角冒号），
不手写模拟。

## 三、Bug 2：恢复终端反馈，但不退回刷屏

### 设计取舍

`25f8087` 静默终端的动机成立（不想让终端持续刷结构化 INFO log），所以**不回滚**，
改成加一条**独立的「控制台通道」**：同一批 `logger.*` 调用，按规则挑一小部分以
纯文本形式送到终端，其余照旧只进文件。call site 一处不用改，i18n 文案沿用现有
`message` 字段。

`src/daobidao/logger.py` 的 `configure_logging()` 新增 `console: bool = True`：

- **放行规则**：`levelno >= WARNING` **一律放行**；`INFO` 只放行事件名白名单。
  —— 这条本身就是本轮 Bug 1 的一道防线：`mic_offline` 是 warning，真按这个规则跑，
  用户第一次按热键就会在终端看到「麦克风离线」，而不是对着毫无反应的键发呆。
- **格式**：纯文本，只打 `event_dict["message"]`（缺失则退回事件名），不带时间戳 /
  logger 名 / key=value；WARNING 以上加一个前缀标记。
- **INFO 白名单**（启动里程碑，每次启动各出现一次，不随使用刷屏）：
  `startup_banner` / `model_preload_start` / `qwen3_download_slow` / `ready` /
  `hotkey_listening` / `shutting_down` / `init_start` / `init_download_model` /
  `init_model_ready` / `init_done`。
- `--verbose` 行为不变（挂完整 `ConsoleRenderer`、全量 DEBUG）；此时不再叠加控制台
  通道，避免同一条打两遍。
- 新增 `--quiet` 关掉控制台通道，退回本轮之前的全静默。

实现上放行规则做成一个 `logging.Filter`，读 `record.msg`（structlog 的
`ProcessorFormatter.wrap_for_formatter` 会把 event_dict 放在这里）。**这一点先写一条
探针测试确认**，若 structlog 版本不是这个形状就退回在 filter 里读 `record.__dict__`。

### 首次下载的「别急，它在下」提示

`src/daobidao/stt/qwen3/qwen3_asr.py` 的 `load()`：在调 `snapshot_download` 之前起一个
`threading.Timer(_SLOW_DOWNLOAD_HINT_S=3.0)`，到点还没返回就打一条
`qwen3_download_slow`（白名单内）：

> 首次运行需要下载模型（0.6B 约 990 MB），网速一般时可能要几分钟，请耐心等待…

返回后 `cancel()`。**用计时器而不是「检查缓存目录在不在」**：modelscope 的缓存根路径
随版本变过（本项目 CLAUDE.md 里写的 `~/.cache/modelscope/hub/`，mypc 上实际是
`~/.cache/modelscope/models/`），自己推路径是又一个会随环境漂移的假设；计时器只依赖
「这次调用有没有很快返回」，任何版本、任何缓存布局下都成立。

### 文案

`src/daobidao/assets/locales/{zh,en,fr}.json` 新增 `stt.download_slow` 一条；
其余复用现有 key。

### 测试（先红后绿）

`tests/test_logger.py` 新增：

- 白名单内的 INFO 上终端、白名单外的 INFO 不上终端
- WARNING / ERROR 一律上终端（拿 `mic_offline` 这个真事件名当用例）
- 终端输出是纯文本，不含时间戳 / `event=` / logger 名
- `console=False`（`--quiet`）时终端零输出，但文件日志照写
- `stderr=True`（`--verbose`）时不重复挂控制台通道

`tests/test_qwen3_asr.py`（或同目录新文件）新增：

- 打桩一个「慢」的 `snapshot_download`（sleep 超过阈值），断言 `qwen3_download_slow`
  被打出来一次
- 打桩一个「快」的，断言**没有**这条（计时器被正确 cancel）

两条都把阈值参数化成很小的值，不让测试真等 3 秒。

## 四、验证

1. `uv run pytest`（全量）+ `uv run ruff check .`
2. **真机回归（mypc）**：把本分支的 wheel 装到那台中文界面机器上，在
   `LANG=zh_CN.UTF-8` 下跑 `daobidao`，确认：
   - 终端能看到 banner → 预加载 → 就绪 → 监听热键
   - 按住热键能真正录到音并出字（不再 `mic_offline`）
   - `--verbose` / `--quiet` 两个开关行为符合预期
3. 本机（macOS）跑一次确认非 Linux 路径没被带坏。

## 五、不做的事

- 不动 Linux 上「pactl 是 probe 唯一权威」的设计。
- 不改录音采集 / STT / 热键逻辑。
- 不去压 modelscope 自己那行 `Downloading 9 files ...` 日志 —— 有了前后我们自己的
  里程碑输出，它不再有误导性，压掉反而少一条真实信息。
- 不为 `main()` 的 CLI 编排补集成测试（既有的空白，本轮不扩大范围，留在局限性里）。

## 六、待人类确认

**终端输出的粒度**，两个选项：

- **A（建议）**：只放行启动里程碑 + 所有 WARNING/ERROR。终端每次启动约 5 行，
  之后安静；出问题时（麦克风离线、热键抓不到、端口被占）会主动说话。
- **B**：在 A 的基础上再放行每次说话的 `recording_start` / `recording_stop` /
  识别结果，接近 `25f8087` 之前的老行为。反馈更足，但连续使用时终端会持续滚动。

我按 **A** 写，若要 B 说一声，白名单加三个事件名即可。
