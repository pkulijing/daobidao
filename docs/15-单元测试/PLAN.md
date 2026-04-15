# 第 15 轮 — 单元测试套（`tests/`）

## Context

项目从 0 轮到 14 轮始终没有自动化测试。每次重构靠"手动跑一遍看有没有炸"，14 轮大规模删代码尤其风险高。13 轮做完 src layout、14 轮把项目压扁到单一发行单元之后，目录结构终于稳定到值得为它配一套测试的程度，BACKLOG.md 里"代码质量 / 测试套 `tests/`"那条等的就是这个时机。

本轮目标：搭起 pytest 框架，覆盖**纯逻辑层**（config / postprocess / version / settings_server）和**带 mock 的边界层**（hotkey 状态机 / autostart 文件生成 / input_method shell-out）。重集成层（STT 推理 / 录音 / overlay）刻意不动。完成后从 BACKLOG.md **删掉**该条目（按 backlog 工作流：完成的条目整条删，不打勾）。

用户已确认的范围决策：

- **范围**：第 1 层 + 第 2 层都做
- **框架**：pytest（不加 pytest-mock，stdlib `unittest.mock` + pytest 的 `monkeypatch` / `tmp_path` fixture 够用）
- **目录**：项目根目录下的 `tests/`
- **CI**：要加，扩展现有 `.github/workflows/build.yml`
- **BACKLOG**：完成后删除"测试套 `tests/`"条目

## 关键设计

### 跨平台测试的核心难题：`pynput` 和 `evdev` 都是平台条件依赖

`pyproject.toml` 里：

```
"evdev>=1.7.0; sys_platform == 'linux'",
"pynput>=1.7.6; sys_platform == 'darwin'",
```

CI 跑在 `ubuntu-24.04` 上 → `pynput` 不会被 `uv sync` 安装 → `whisper_input.backends.hotkey_macos` / `input_macos` 在 linux 上 import 即失败。但我们**就是要测这两个文件**。

**方案**：`tests/conftest.py` 在导入任何 `whisper_input` 模块**之前**，往 `sys.modules` 里**强制注入**伪造的 `pynput` / `pynput.keyboard` 和 `evdev` / `evdev.ecodes` 模块。强制注入（不是 `if "pynput" not in sys.modules`）保证 macOS 本机跑测试时也用 fake，避免不小心调到真 pynput 的 `Listener.start()` 真的开全局键盘监听。

伪造模块只需要满足"被 import 时不报错 + 提供测试用到的常量/类名"，不需要任何真实行为：

```python
# tests/conftest.py
import sys, types

def _install_fake_pynput():
    pynput = types.ModuleType("pynput")
    keyboard = types.ModuleType("pynput.keyboard")
    class Key:
        ctrl = "ctrl"; ctrl_r = "ctrl_r"
        alt = "alt"; alt_r = "alt_r"
        cmd = "cmd"; cmd_r = "cmd_r"
        caps_lock = "caps_lock"
        f1 = "f1"; f2 = "f2"; f5 = "f5"; f12 = "f12"
    class Listener:
        def __init__(self, on_press=None, on_release=None): ...
        def start(self): ...
        def stop(self): ...
    class Controller:
        def press(self, k): ...
        def release(self, k): ...
    keyboard.Key = Key
    keyboard.Listener = Listener
    keyboard.Controller = Controller
    pynput.keyboard = keyboard
    sys.modules["pynput"] = pynput
    sys.modules["pynput.keyboard"] = keyboard

def _install_fake_evdev():
    # 类似的，提供 ecodes 常量 + InputDevice / list_devices 占位
    ...

_install_fake_pynput()
_install_fake_evdev()
```

测试 hotkey 状态机时**永远不调 `.start()`**，只直接调 `_on_hotkey_press` / `_on_hotkey_release` / `_on_combo_detected` / `_on_delayed_press` 这几个内部方法，验证 `on_press` / `on_release` callback 被调用的次数和顺序。

### 文件系统隔离：tmp_path 而不是真 ~/.config / ~/Library

凡是会读写文件的测试（`config_manager`、`autostart_*`），全部用 pytest 的 `tmp_path` fixture + `monkeypatch` 改模块级常量（`AUTOSTART_DIR` / `AUTOSTART_FILE` / `CONFIG_DIR`）指向临时目录。绝不允许任何测试触碰真实的 `~/.config/whisper-input/` 或 `~/Library/LaunchAgents/`。

### subprocess 隔离：`monkeypatch.setattr(subprocess, "run", ...)`

`autostart_macos._launchctl` 和 `input_*.type_text` 都会 shell out。测试时把 `subprocess.run` 替换成一个记录调用参数的 fake，验证调用了正确的命令行而不是真的执行。

### 不测的部分（明确写下来避免未来误以为是遗漏）

- `stt/sense_voice.py` 推理路径：要 ~231MB 模型 + onnxruntime + numpy，CI 上不值得
- `stt/_wav_frontend.py`：纯数值代码，需要参考音频 + 参考特征，setup 成本太高
- `stt/_tokenizer.py`：需要 BPE 模型文件
- `recorder.py`：需要真麦克风 / sounddevice
- `overlay_*.py`：需要 GTK / Cocoa 渲染
- `__main__.py`：编排层，端到端测试范围
- 真实键盘 / TCC 权限路径

## 改动清单

### 新增文件

```
tests/
├── __init__.py                  # 空文件,声明为包
├── conftest.py                  # 注入 fake pynput / evdev
├── test_config_manager.py
├── test_postprocess.py
├── test_version.py
├── test_settings_server.py
├── test_hotkey_combo.py
├── test_autostart_macos.py
├── test_autostart_linux.py
└── test_input_method.py

docs/15-单元测试/
├── PROMPT.md                    # 需求文档(简版)
├── PLAN.md                      # 本计划文件的项目内副本
└── SUMMARY.md                   # 执行完成后写
```

### 修改文件

- `pyproject.toml`：`[dependency-groups] dev` 加 `pytest>=8.0`，再加 `[tool.pytest.ini_options] testpaths = ["tests"]`
- `.github/workflows/build.yml`：在现有 `lint` job 的 `Ruff check` 步骤之后加两步：
  - `uv sync --group dev`
  - `uv run pytest -q`
  - job 名从 `lint` 改成 `lint-and-test`（README badge 通过 workflow 名 `Build` 引用，不会断）
- `BACKLOG.md`：删除第 200-228 行的"测试套 `tests/`"整块（含小标题）
- `CLAUDE.md`：在 `## Commands` 段把 `No automated test suite exists` 那句改成跑测试的指令（`uv run pytest`），并把"测试什么 / 不测什么"用 1-2 句话说明

### 各测试文件具体覆盖项

#### `tests/test_config_manager.py`
针对 [src/whisper_input/config_manager.py](../../Developer/whisper-input/src/whisper_input/config_manager.py)
- `_deep_merge`：嵌套字典深合并，override 覆盖 base，非 dict 值直接替换
- `ConfigManager(config_path=tmp)` 显式路径：文件不存在 → 用 `DEFAULT_CONFIG`；文件存在 → 与默认值深合并
- `get("a.b.c")` 点号路径：存在 / 不存在（返回 default）
- `set("a.b.c", v)` 点号路径：中间节点不存在时自动创建
- `save()` 后再 `load()`：值能正确读回（往返）
- `_generate_yaml(DEFAULT_CONFIG)`：assert 含若干关键行（`engine: sensevoice`、`hotkey_linux:`、`audio:`、`overlay:` 等）
- `_resolve_path` 在 dev 模式（monkeypatch `_find_project_root` 返回 tmp_path）/ installed 模式（monkeypatch `CONFIG_DIR` 指向 tmp_path）下分别返回正确路径，且首次调用时会从 package data 复制 `config.example.yaml`

#### `tests/test_postprocess.py`
针对 [src/whisper_input/stt/_postprocess.py](../../Developer/whisper-input/src/whisper_input/stt/_postprocess.py) 的 `rich_transcription_postprocess`
- 中性中文：`'<|zh|><|NEUTRAL|><|Speech|><|withitn|>欢迎大家来体验达摩院推出的语音识别模型。'` → `'欢迎大家来体验达摩院推出的语音识别模型。'`（FunASR 官方文档示例）
- 含 HAPPY 情感 → 输出尾部带 😊
- 含 Applause 事件 → 输出头部带 👏
- `<|nospeech|><|Event_UNK|>` → `❓`
- 英文 + `<|en|>` 标签 → 标签被剥
- 空字符串 → 空字符串
- 多段 `<|lang|>` 拼接：相邻同情感不重复 emoji

#### `tests/test_version.py`
针对 [src/whisper_input/version.py](../../Developer/whisper-input/src/whisper_input/version.py)
- `__version__` 通过 `importlib.metadata.version("whisper-input")` 拿到的值非空（dev 安装下应该是 `pyproject.toml` 里的版本号）
- `_read_commit()` 三条路径：
  1. `_commit.txt` 存在 → 返回其内容（用 monkeypatch 让 `files()` 返回 tmp_path 里写好 `_commit.txt` 的目录）
  2. `_commit.txt` 不存在但是在 git 仓库 → subprocess 返回 HEAD（用 monkeypatch 替换 subprocess.run）
  3. 都不行 → 返回 `""`

#### `tests/test_settings_server.py`
针对 [src/whisper_input/settings_server.py](../../Developer/whisper-input/src/whisper_input/settings_server.py)
- `_get_settings_html()`：返回的 HTML 包含 `<title>Whisper Input 设置</title>`、含 `HOTKEYS = [...]`（assert 占位符已替换）
- 启动一个真实 `SettingsServer`：用 ConfigManager + tmp_path 配置，端口选 0 或 51230 + 1（避免冲突）。**`SettingsServer.__init__` 当前写死接收 `port` 参数，构造时传一个高位空闲端口即可。** 然后用 stdlib `http.client` 发请求：
  - `GET /` → 200 + text/html
  - `GET /api/config` → 200 + JSON 含 `engine` 字段
  - `POST /api/config` `{"sensevoice.language": "en"}` → 200 → 再 GET 验证持久化
  - `POST /api/config/reset` → 200 → GET 拿到 DEFAULT_CONFIG
  - `GET /api/autostart` → 200 + `{"enabled": ...}`（用 monkeypatch 把 `_is_autostart_enabled` 替换成返回 True/False 的 stub）
  - `POST /api/autostart` `{"enabled": true}` → 200，且我们注入的 stub 被以正确参数调用
- `/api/quit` 和 `/api/restart` 不直接测，因为它们启动 `threading.Timer` 调 `os.kill` / `os.execv`。改为 monkeypatch `os.kill` 和 `os.execv` 成 no-op 后，发请求确认返回 200 即可

#### `tests/test_hotkey_combo.py`
针对 [src/whisper_input/backends/hotkey_macos.py](../../Developer/whisper-input/src/whisper_input/backends/hotkey_macos.py) 和 [hotkey_linux.py](../../Developer/whisper-input/src/whisper_input/backends/hotkey_linux.py)
- 两个文件的 `HotkeyListener` 类几乎对称，写一个参数化测试（`@pytest.mark.parametrize("module_name", ["hotkey_macos", "hotkey_linux"])`），通过 `importlib.import_module` 拿到 `HotkeyListener` 类
- 测试用例（每个 listener 各跑一遍）：
  1. **修饰键 + 提前释放**：构造 listener with `KEY_RIGHTCTRL` → 调 `_on_hotkey_press()` → 立即调 `_on_hotkey_release()` → 取消定时器 → `on_press` callback 不应被调用
  2. **修饰键 + 等待延时后释放**：把 `COMBO_DELAY` monkeypatch 成 0.05 → 调 `_on_hotkey_press()` → `time.sleep(0.1)` → 调 `_on_hotkey_release()` → `on_press` 调用 1 次，`on_release` 调用 1 次
  3. **修饰键 + 组合键检测**：调 `_on_hotkey_press()` → 调 `_on_combo_detected()` → 等延时过去 → `on_press` 不应被调用
  4. **非修饰键**：构造 listener with `KEY_F1` → 调 `_on_hotkey_press()` → 立即 `on_press` 调用（无延迟）→ 调 `_on_hotkey_release()` → `on_release` 调用
  5. **不支持的键**：`HotkeyListener("KEY_NONEXISTENT", ...)` → `ValueError`
- 永远不调 `.start()`，永远不真起 `Listener` / `_listen_loop` 后台线程
- mac 那边 `_on_hotkey_press` 实际接受 pynput Key 对象，构造时用 `SUPPORTED_KEYS["KEY_RIGHTCTRL"]` 拿到（在 fake pynput 下就是字符串 `"ctrl_r"`，不影响测试逻辑）

#### `tests/test_autostart_macos.py`
针对 [src/whisper_input/backends/autostart_macos.py](../../Developer/whisper-input/src/whisper_input/backends/autostart_macos.py)（**注意：纯 stdlib，无 pyobjc 依赖，可以在 linux CI 上跑**）
- `_xml_escape("a&b<c>")` → `"a&amp;b&lt;c&gt;"`
- `_program_arguments()`：
  - monkeypatch `sys.prefix` 指向一个 tmp 目录，里面创建 `bin/whisper-input` 文件 → 返回 `[那个文件路径]`
  - sys.prefix 下没有 → 返回 `[sys.executable, "-m", "whisper_input"]`
- `_build_plist()` 的输出可被 stdlib `plistlib.loads` 解析；解析后 `Label == "com.whisper-input"`、`RunAtLoad == True`、`KeepAlive == False`、`ProgramArguments` 含我们造的命令
- monkeypatch `AUTOSTART_DIR` 和 `AUTOSTART_FILE` 指向 tmp_path
- monkeypatch `subprocess.run`（`_launchctl` 内部用）成 no-op
- `is_autostart_enabled()`：写入文件前 False，写入后 True
- `set_autostart(True)` → 文件存在且内容是 `_build_plist()` 的输出
- `set_autostart(False)` → 文件被删除，且 `subprocess.run` 被以 `["launchctl", "bootout", ...]` 调用过

#### `tests/test_autostart_linux.py`
针对 [src/whisper_input/backends/autostart_linux.py](../../Developer/whisper-input/src/whisper_input/backends/autostart_linux.py)
- `_load_desktop_template()` 返回的字符串包含 `[Desktop Entry]`、`Exec=whisper-input`、`Name=`
- monkeypatch `AUTOSTART_DIR` / `AUTOSTART_FILE` 指向 tmp_path
- `is_autostart_enabled()`：写入前 False，写入后 True
- `set_autostart(True)`：文件存在且内容等于 `_load_desktop_template()` 的输出
- `set_autostart(False)`：文件被删除（即使再调一次也不报错）

#### `tests/test_input_method.py`
针对 [src/whisper_input/backends/input_macos.py](../../Developer/whisper-input/src/whisper_input/backends/input_macos.py) 和 [input_linux.py](../../Developer/whisper-input/src/whisper_input/backends/input_linux.py)
- **macOS 部分**（fake pynput 已在 conftest 注入，所以 import 可行）：
  - `type_text("")` → `subprocess.run` 0 次调用
  - `type_text("hello")`：mock subprocess.run；assert pbpaste / pbcopy 各被以正确 input 调用，且 `_keyboard.press / release` 调用顺序是 cmd → v → release v → release cmd（fake Controller 记录调用序列）
- **Linux 部分**：
  - `type_text("")` → 不调 subprocess
  - `type_text("中文", method="clipboard")`：mock subprocess.run；assert 调用了 `xclip -selection clipboard` 和 `xclip -selection primary`（写入），以及 `xdotool key --clearmodifiers shift+Insert`
  - `type_text("text", method="xdotool")`：assert 只调用 `xdotool type --clearmodifiers -- text`

## CI 集成

`.github/workflows/build.yml` 改后形态（增量改动，保留现有 ruff 步骤）：

```yaml
name: Build
on:
  push:
    branches: [master, refactor/**]
  pull_request:
  workflow_dispatch:

jobs:
  lint-and-test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
      - name: Ruff check
        run: uvx ruff check .
      - name: Install dev deps
        run: uv sync --group dev
      - name: Run tests
        run: uv run pytest -q
```

**只跑 ubuntu**（不加 macos-latest 矩阵）：
- 第一版优先证明 conftest 的 fake-injection 方案可行
- macos runner 比 ubuntu runner 贵 10×，不值得在 v1 就开
- 后续如果发现 darwin-only 路径回归（比如 `_program_arguments` 在 darwin 上 `sys.prefix` 路径不一样），再加 macos 矩阵

`uv sync --group dev` 会装 ruff + pytest，但**不会**装 darwin-only 的 pynput / pyobjc，conftest 的 fake 兜住。

## 验证

执行完成后按这个清单验证：

1. **本地 macOS**：`uv run pytest -q` 在干净 venv 里全绿
2. **本地 macOS**：故意往 `_postprocess.py` 里改一个 emoji 映射，pytest 应失败 → 还原
3. **本地 macOS**：故意把 `set_autostart(True)` 里写文件那行注释掉，pytest 应失败 → 还原
4. **CI**：push 后 GitHub Actions 上 `lint-and-test` job 全绿
5. **现有 ruff 检查**仍然绿：`uv run ruff check .`
6. **`uv run whisper-input --help`** 不受影响（确保 conftest 的 fake 没污染主程序运行路径）
7. **BACKLOG.md** 第 200-228 行被整块删除（含"测试套 `tests/`"小标题）
8. **docs/15-单元测试/SUMMARY.md** 写完，按项目 SUMMARY 模板：背景 / 实现方案 / 局限性 / 后续 TODO

## 局限性 / 不在本轮范围

- **STT 推理 / 录音 / overlay 完全不测**（见上文"不测的部分"），靠"上线后手动跑一次"兜底
- **真实键盘 / TCC 权限路径**完全不测，得在 macOS 实机上手动验证
- **macOS CI runner** 不开，conftest 的 fake-injection 方案在真 darwin 上是否完全等价于真 pynput 还需要本地 macOS 跑一次确认
- **覆盖率工具**（coverage.py）不接入 v1。下一轮想加再说

## 后续 TODO（执行完入 BACKLOG.md，不入本计划）

- macOS CI runner 矩阵
- coverage.py + codecov 上传徽章
- `stt/sense_voice.py` 端到端冒烟测试，用一个极小的本地 wav fixture（要解决模型缓存策略）
- 把 hotkey 测试从"调 internal 方法"提升到"通过 fake Listener 注入合成事件"
