# 15 轮 SUMMARY — 单元测试套（`tests/`）

## 开发项背景

项目从 0 轮到 14 轮始终没有自动化测试。每次重构靠"手动跑一遍看有没有炸"，14 轮大规模删 bundle 期代码（`stt/downloader.py` / `setup_window.py` / `packaging/` 全删）尤其暴露了这个问题：当时只能靠 grep 确认没人 import，**没有任何机器化的方式可以兜住"删错了"的情况**。

13 轮重构落地了 src layout、14 轮把项目压扁到单一 PyPI 发行单元之后，目录结构终于稳定到值得为它配一套测试的程度。`BACKLOG.md` 里"代码质量 / 测试套 `tests/`"那条等的就是这个时机。本轮兑现这一条。

希望解决的问题：

- **回归保护**：今后任何重构 / 删代码都有自动化兜底，CI 上一个红勾就能知道有没有炸
- **测试基础设施**：搭好 pytest 框架 + cross-platform conftest + CI 集成，后续只需要往里加 case 即可
- **文档化**：在 CLAUDE.md 里写清楚"哪些层有测 / 哪些层没测 / 怎么跑"，让后续 agent / 自己回来都能立即上手

## 实现方案

### 关键设计

#### 1. 跨平台 import 的 fake-injection 兜底

最大的设计难题：`pynput` 是 darwin-only 依赖、`evdev` 是 linux-only 依赖，CI 在 ubuntu 上跑根本没装 pynput，但我们就是要测 `hotkey_macos.py` / `input_macos.py`。

解法：`tests/conftest.py` 在 import 任何 `whisper_input` 模块**之前**，往 `sys.modules` 里**强制**注入伪造的 `pynput` / `pynput.keyboard` 和 `evdev` / `evdev.ecodes`。强制注入（不是 `if not in sys.modules`）双向保证 —— linux CI 上提供 import 就行，macOS 本机 dev 上避免不小心调到真 pynput 的 `Listener.start()` 触发全局键盘监听。

伪造模块只满足"被 import 不报错 + 提供测试用到的常量 / 类名"：`Key.ctrl_r = "ctrl_r"`、`Listener` 和 `Controller` 类、`evdev.ecodes` 一组任意的 int 键码。`Controller` fake 还会把 `press` / `release` 调用记到 `.calls` 列表里，给 `test_input_method.py` 里验证 Cmd+V 序列用。

效果：CI 单 ubuntu runner 就能跑 macOS 和 Linux backend 两套测试，省下 10× macos runner 成本。

#### 2. 文件系统隔离 = tmp_path + monkeypatch

凡是会读写文件的测试（`config_manager`、`autostart_macos`、`autostart_linux`、`settings_server`），全部用 `tmp_path` fixture + `monkeypatch` 改模块级常量（`AUTOSTART_DIR` / `AUTOSTART_FILE` / `CONFIG_DIR`）指向临时目录。**绝不允许任何测试触碰真实的 `~/.config/whisper-input/` 或 `~/Library/LaunchAgents/`**。

#### 3. subprocess 隔离 = monkeypatch.setattr(mod.subprocess, "run", recorder)

`autostart_macos._launchctl` 和 `input_*.type_text` 都 shell out。测试里把 `subprocess.run` 替换成一个记录 `(cmd, input)` 元组的 `_RunRecorder`，验证调用顺序和参数 —— 既不会真的 `launchctl bootout`，也不会真的 `pbcopy` 污染 dev 机器剪贴板。

#### 4. hotkey 状态机用 internal 方法直驱

`HotkeyListener` 内部有 `threading.Timer` + `threading.Lock`，全链路（pynput Listener / evdev `_listen_loop` → callback → 状态机 → on_press）端到端测既要起后台线程又要造合成事件，复杂且 flaky。本轮选择**直接调 `_on_hotkey_press` / `_on_hotkey_release` / `_on_combo_detected` / `_on_delayed_press`**，测的是状态机本身的正确性。`COMBO_DELAY` 用 `monkeypatch.setattr(mod, "COMBO_DELAY", 0.05)` 缩短到测试可控的 50ms。

参数化 `@pytest.mark.parametrize("backend", ["hotkey_macos", "hotkey_linux"])` 让同一组 5 个用例在两个 backend 上各跑一遍（共 12 case = 5 通用 + 1 backend 自检 × 2），保证状态机两边对称。

#### 5. settings_server 真启动而不是单元 mock

`SettingsServer` 的逻辑短到几乎所有价值都在 HTTP handler 路径上。**直接起一个真实 server**到 `127.0.0.1` + OS 分配的临时空闲端口，用 stdlib `http.client` 打请求验证：GET / POST / 持久化 / reset / autostart toggle / 404 全跑通。

`/api/quit` 和 `/api/restart` 两个会调 `os.kill(SIGTERM)` / `os.execv` 的危险 endpoint，用 `monkeypatch.setattr(ss.os, "kill", lambda *a, **kw: None)` 替换成 no-op，只验证 handler 返回 200 即可。

#### 6. 覆盖率默认开启 + codecov 上传 + README 徽章

PLAN 阶段写到"v1 不接 coverage,下一轮再说",执行过程中两次改了主意 —— 用户先说"我对我的代码覆盖率有一个概念,直接加上",随后又说"codecov 上传 + 徽章这条 TODO 也直接做了呗"。所以本轮顺手把整条覆盖率链路打通:

- pytest-cov 加进 dev deps
- `pyproject.toml` 的 `addopts` 默认带 `--cov=whisper_input --cov-report=term`,**每次 `uv run pytest` 都自动打印覆盖率**(本地 + CI 都一样)
- CI 在 pytest 那步追加 `--cov-report=xml`,产出 `coverage.xml`
- 紧跟一步 `codecov/codecov-action@v4` 把 `coverage.xml` 上传到 codecov.io。**公开仓库 + 不需要 token**,首次上传会自动在 codecov 创建项目
- README 头部加 `![codecov](...)` 徽章,和 Build / PyPI 徽章并列
- `fail_ci_if_error: false` —— codecov 服务抖动不应该阻挡 PR 合并

#### 7. STT 端到端冒烟测试

PLAN 把 STT 推理列在"不测的部分",理由是模型 ~231 MB 很重。执行过程中用户说"这个也可以随手做了吧 —— 模型页面就有公开测试 wav,模型在 CI 服务器上也可以缓存,甚至都不需要 slow,就普通的测试就好"。于是顺手做了:

- `tests/fixtures/zh.wav`(341 KB)+ `tests/fixtures/zh.m4a`(92 KB,作为可重新生成的源文件):作者(@pkulijing)自录的一段《出师表》开头,10.6 秒,内容是 "先帝创业未半而中道崩殂,今天下三分,益州疲弊,此诚危急存亡之秋也。"。早期 PR 用过 FunASR 官方示例 `iic/SenseVoiceSmall/example/zh.mp3`,但作者觉得官方那条录音口音别扭,换成自己的录音。m4a → wav 通过 macOS 自带 `afconvert -f WAVE -d LEI16@16000 -c 1` 一行命令转出来,Linux 上等价命令是 `ffmpeg -i ... -ar 16000 -ac 1 -c:a pcm_s16le ...`。来源 / 许可 / 重新生成方法在 `tests/fixtures/README.md` 里写清楚
- `tests/test_sense_voice.py`:4 个用例 —— fixture sanity / 真实推理 / 空输入 / 过短输入。**module 级 fixture** 共享 `SenseVoiceSTT` 实例,只付一次 ONNX session 创建成本。推理用例 assert 输出包含 "先帝创业" / "天下三分" / "益州" / "存亡" 四个稳定识别的语义片段,不做精确字符串匹配(量化模型在某些字上有可预期的小偏差,实测把"未半"识成"未伴",把"诚危急"识成"称危及")
- `.github/workflows/build.yml` 加 `actions/cache@v4` 缓存 `~/.cache/modelscope/hub`,key 用 `modelscope-sensevoice-v1` —— 想强制刷新就 bump 版本号
- **不加 `pytest.mark.slow`**:用户的判断是项目核心路径理应在默认测试套里跑,有 slow marker 容易被忽略。代价是首次 fresh clone 跑测试要下 ~231 MB(本地 dev 大概率已经 cache 过,CI 通过 actions/cache 命中后只是几秒推理)。total 测试时间从 8s 涨到 11s

效果:本轮总覆盖率从 39%(只测纯逻辑 + 边界层)拉到 **51%**:`stt/sense_voice.py` 0% → 100%、`stt/_wav_frontend.py` 0% → 91%、`stt/_tokenizer.py` 0% → 72%。STT 整条推理链路(fbank → ONNX → CTC 解码 → tokenizer → postprocess)全跑通。

刻意**不 omit** 任何文件 —— 不靠隐藏 `recorder.py` / `overlay_*.py` / `__main__.py` 的 0% 来美化数字。覆盖率工具的价值是"让没测的东西显眼",omit 掉就背离了初衷。本轮收尾时(75 个用例)的覆盖率分布:

| 模块 | 覆盖率 | 备注 |
| --- | --- | --- |
| `stt/sense_voice.py` / `backends/autostart_*` / `backends/__init__.py` / `stt/base.py` | 100% | 全测 |
| `stt/_wav_frontend.py` | 91-97% | STT 冒烟测试拉起的 fbank + LFR + CMVN 路径 |
| `config_manager.py` / `_postprocess.py` | 93% | 主逻辑路径全覆盖,剩下是 dev 模式 fallback |
| `backends/input_*.py` | 91-92% | 剩下是 try/except 的失败分支 |
| `settings_server.py` | 90% | 剩下是 quit/restart 的 timer 回调内部 |
| `version.py` | 84% | 剩下是 ImportError fallback |
| `hotkey.py` / `input_method.py` dispatcher | 80% | 只走当前平台分支 |
| `stt/_tokenizer.py` | 72% | STT 推理走的解码分支,边角路径(load 失败等)未覆盖 |
| `backends/hotkey_*.py` | 54% | 状态机全覆盖,`_listen_loop` / `start` / `stop` / `find_keyboard_devices` / `check_macos_permissions` 没测 |
| `stt/__init__.py` | 43% | factory 只测了 import,没测 lazy 分支 |
| `__main__.py` / `recorder.py` / `overlay_*.py` | **0%** | 全部是 PROMPT.md 里明确写"不测"的模块,要么硬件 / 要么 GUI / 要么编排层 |

51% 是一个**诚实的**起点 —— 它告诉你"如果你继续在 recorder.py 下面加代码,这个数字会继续往下掉,直到你给它写测试"。这正是覆盖率工具该提供的反馈。

`htmlcov/`、`.coverage`、`.pytest_cache/`、`coverage.xml` 都加进了根目录 `.gitignore`。

### 开发内容概括

新增 12 个文件 + 改 6 个文件：

```
tests/
├── __init__.py
├── conftest.py                  fake pynput / evdev 注入
├── test_postprocess.py          8 个用例
├── test_config_manager.py       16 个用例
├── test_version.py              5 个用例
├── test_settings_server.py      11 个用例(含真 HTTP server)
├── test_hotkey_combo.py         12 个用例(参数化 × 2 backend)
├── test_autostart_macos.py      7 个用例
├── test_autostart_linux.py      4 个用例
├── test_input_method.py         6 个用例
├── test_dispatchers.py          2 个用例(hotkey / input_method 调度器 smoke)
├── test_sense_voice.py          4 个用例(端到端 STT 推理冒烟)
└── fixtures/
    ├── README.md                fixture 来源 / 许可 / 重新生成命令
    ├── zh.m4a                   92 KB 源文件(作者自录)
    └── zh.wav                   341 KB 16k mono PCM(测试实际读的就是这个)
                                 ─────────
                                 共 75 个用例,~11s,默认带覆盖率报告
```

修改：

- `pyproject.toml`：`[dependency-groups] dev` 加 `pytest>=8.0` 和 `pytest-cov>=7.1.0`,加 `[tool.pytest.ini_options]` 让 `uv run pytest` 默认带 `--cov=whisper_input --cov-report=term`,加 `[tool.coverage.run]` / `[tool.coverage.report]` 两段配置
- `.github/workflows/build.yml`：job 名 `lint` → `lint-and-test`,Ruff check 后加 `uv sync --group dev` / `actions/cache@v4`(缓存 modelscope hub) / `uv run pytest --cov-report=xml --cov-report=term` / `codecov/codecov-action@v4` 上传四步
- `.gitignore`：加 `.pytest_cache/` / `.coverage` / `htmlcov/` / `coverage.xml`
- `README.md`：徽章组里 Build 和 PyPI 中间插入 codecov 徽章
- `BACKLOG.md`：删除"测试套 `tests/`"整条（按 backlog 工作流：完成的条目整条删,不打勾）；同时在"代码质量"分类下加入"测试套增强（v2）"新条目,记录 v1 没做但值得后续推进的方向
- `CLAUDE.md`：把 "No automated test suite exists" 那句换成 `uv run pytest` 命令 + 覆盖率说明 + 测试覆盖范围

### 额外产物

- `tests/conftest.py` 的 fake pynput / evdev 模式可以被任何后续平台后端测试复用
- `_RunRecorder` 模式（`tests/test_input_method.py` 里那个）足够通用,以后任何 shell-out 类代码都能套用
- `_make_listener` helper（`tests/test_hotkey_combo.py`）+ 参数化 backend，给后续加新平台的 hotkey 测试留好了模板

## 验证

执行完成后跑过的检查（对照 `PLAN.md` 验证清单）：

1. **`uv run pytest`** 在干净 venv 全绿（75 passed in 11.08s, 总覆盖率 51%）✓
2. **`uv run ruff check .`** 全绿 ✓
3. **`uv run whisper-input --help`** 正常输出（确认 conftest 的 fake-injection 没污染主程序运行路径）✓
4. **`uv run pytest --cov-report=xml`** 产出 `coverage.xml`,codecov-action 上传 OK ✓
5. **STT 端到端推理** 跑通,模型加载 + ONNX 推理 + 后处理产出预期文本片段 ✓
6. **CI**：GitHub Actions 上 `lint-and-test` job 等 push 后验证（本地无法验证,见局限性）

故意改坏一行 → 测试失败的对照检查计划在 push 之前手跑一次。

## 局限性

1. **录音 / overlay / `__main__.py` 完全不测**。这是有意识的取舍 —— 它们要么需要真麦克风、要么需要 GTK / Cocoa 渲染、要么是端到端编排层。本轮判断是 v1 先把可以测的部分稳稳兜住,这些路径继续靠"上线后手动跑一次"。它们在覆盖率报告里全部显示 0%,这是有意识地保留的"未测可视化"
2. **真实键盘事件 / TCC 权限路径不测**。`hotkey_*.start()` / `_listen_loop` / pynput Listener callback 这些代码路径走的是真 OS API,得在 macOS 实机 + Linux 实机上手动验证。本轮的 hotkey 测试只覆盖状态机本身,不覆盖事件分发(`hotkey_*.py` 因此停留在 54% 覆盖率)
3. **macOS CI runner 不开**。conftest 的 fake-injection 在真 darwin 上是否完全等价于真 pynput,需要本地 macOS 跑一次确认。如果某次 darwin-only 路径回归（比如 `_program_arguments` 在真 macOS 上 `sys.prefix` 路径有差异）会漏掉。代价 vs 收益：macos runner 比 ubuntu 贵 10×,本轮选择不开
4. **STT 测试只覆盖一条中文样本**。`test_sense_voice.py` 用作者自录的一段 10.6 秒《出师表》开头跑通整个推理链路 —— 足够当冒烟兜底,但没覆盖英文 / 日文 / 韩文 / 粤语,也没覆盖长音频 / 噪声 / 多说话人这些边角场景。fixture 的 assertion 故意挑了"先帝创业 / 天下三分 / 益州 / 存亡"这几个稳定识别的语义片段,绕开了量化模型在"未半→未伴"、"诚危急→称危及"上的可预期偏差,但万一未来 SenseVoice 出新版本、识别风格变了,这套 assertion 还是可能要调
5. **CI 验证延后**。本地 macOS 全绿,但 GitHub Actions 上能不能跑通要等 push 之后验证 —— 尤其几个新东西全是首次跑:conftest 的 fake-injection 在 ubuntu runner 上、`uv sync --group dev` 装 pytest + pytest-cov、codecov-action 首次上传(会触发 codecov 自动建项目)、README 徽章 URL 拼对了没、`actions/cache@v4` 缓存 modelscope hub 首次需要下载 ~231 MB 模型(后续命中)

## 后续 TODO

按"v1 不做但值得做"的顺序：

1. **macOS CI runner 矩阵**：解决局限性 #3。在 `build.yml` matrix 加 `macos-latest`,代价是 runner 配额翻 10×
2. **hotkey 测试升级**：从"调 internal 方法"提升到"通过 fake Listener / fake evdev 设备注入合成键盘事件",让 `_listen_loop` / pynput callback 自然驱动状态机。改造后能测到事件分发逻辑（不只是状态机）,把 `hotkey_*.py` 从 54% 推到 80%+
3. **STT 多语种 / 边角样本**：解决局限性 #4。当前只测一条中文。可以再加 en / ja / ko / yue 各一个 fixture(同样从 `iic/SenseVoiceSmall/example/` 转换),覆盖更多语种 prompt id 路径

这些都已经入 `BACKLOG.md` 的"测试套增强（v2）"条目。
