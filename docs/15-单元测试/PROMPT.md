# 15 轮需求：补齐单元测试套（`tests/`）

## 背景

从 0 轮到 14 轮，项目始终没有自动化测试。每次重构靠"手动跑一遍看有没有炸"，14 轮大规模删 bundle 期代码尤其暴露了这个问题：当时只能靠 grep 确认没人 import 被删的 `stt/downloader.py` / `setup_window.py`，**没有任何机器化的方式可以兜住"删错了"的情况**。

13 轮重构落地了 src layout、14 轮把项目压扁到单一 PyPI 发行单元之后，目录终于稳定到值得为它配一套测试的程度。`BACKLOG.md` 里"代码质量 / 测试套 `tests/`"那条等的就是这个时机。

## 目标

搭起一套 pytest 框架，覆盖两层代码：

1. **纯逻辑层（最容易，ROI 最高）**
   - `config_manager.py`：YAML 默认值合并、文件读写、点号路径 get/set、dev / installed 模式的路径解析
   - `stt/_postprocess.py` 的 `rich_transcription_postprocess`：纯字符串处理，FunASR 官方有已知输入 / 输出对
   - `version.py`：`_read_commit()` 的三条 fallback 路径
   - `settings_server.py`：用 stdlib `http.client` 打一个真实启动的 server，覆盖所有 REST handler

2. **带 mock 的边界层**
   - `backends/hotkey_macos.py` / `hotkey_linux.py` 的 300ms combo 检测状态机（这是真实存在过 bug 的地方，必须兜住）
   - `backends/autostart_macos.py` / `autostart_linux.py` 的文件生成路径
   - `backends/input_macos.py` / `input_linux.py` 的 shell-out 顺序

测试要在 GitHub Actions CI 上每次 push / PR 自动跑。完成后从 `BACKLOG.md` 里**整条删掉**"测试套 `tests/`"那一节（按 backlog 工作流：完成的条目不打勾，整条删）。

## 非目标（明确不做）

- **不测 STT 推理路径**（`stt/sense_voice.py`）：要 ~231 MB 模型 + onnxruntime + numpy，CI 上不值得
- **不测 `stt/_wav_frontend.py`**：纯数值代码需要参考音频 + 参考特征，setup 成本太高
- **不测 `stt/_tokenizer.py`**：需要 BPE 模型文件
- **不测 `recorder.py`**：需要真麦克风 / sounddevice
- **不测 `overlay_*.py`**：需要 GTK / Cocoa 渲染
- **不测 `__main__.py`**：编排层，端到端范围
- **不测真实键盘事件 / TCC 权限路径**：得在实机上手动验证
- **不上 coverage.py / codecov 徽章**：v1 先证明测试套能跑起来，覆盖率工具下一轮再说
- **不开 macOS CI runner 矩阵**：`ubuntu-24.04` 单 runner 跑通就行（macos runner 比 ubuntu 贵 10×）

## 用户已经定好的范围决策

在 PLAN 阶段之前，用户已经明确：

- **范围**：第 1 层 + 第 2 层都做（不只是第 1 层）
- **框架**：pytest（不加 pytest-mock，stdlib `unittest.mock` + pytest 的 `monkeypatch` / `tmp_path` fixture 够用）
- **目录**：项目根目录下的 `tests/`
- **CI**：要加，扩展现有 `.github/workflows/build.yml`
- **BACKLOG**：完成后删除"测试套 `tests/`"条目

## 待 Plan 阶段明确的问题

1. **跨平台 import**：`pynput` 是 darwin-only 依赖，`evdev` 是 linux-only 依赖。CI 在 ubuntu 上跑，怎么 import `hotkey_macos` / `input_macos` 而不挂？
2. **文件系统隔离**：所有写文件的测试怎么保证不污染真实的 `~/.config/whisper-input/` 和 `~/Library/LaunchAgents/`？
3. **subprocess 隔离**：`autostart_macos._launchctl` 和 `input_*.type_text` 都会 shell out，怎么验证调用了正确的命令行而不是真的执行？
4. **hotkey 状态机的并发**：状态机里有 `threading.Timer` + `threading.Lock`，怎么在测试里跑得稳定不 flaky？
5. **CLAUDE.md 怎么改**：当前明确写着 "No automated test suite exists"，本轮要把这句换掉

这些问题在 `PLAN.md` 里给出方案。

## 验收标准

1. `uv run pytest -q` 在本地 macOS dev 环境全绿
2. 现有 `uv run ruff check .` 仍然全绿
3. GitHub Actions 上 `lint-and-test` job 全绿
4. 故意往 `_postprocess.py` 改一个 emoji 映射 → pytest 失败 → 还原；故意把 `set_autostart(True)` 的写文件那行注释掉 → pytest 失败 → 还原
5. `uv run whisper-input --help` 不受影响（确保 conftest 的 fake-injection 没污染主程序运行路径）
6. `BACKLOG.md` 里"测试套 `tests/`"整条被删掉
7. `CLAUDE.md` 的"No automated test suite exists"被替换成跑测试的指令

## 参考上下文

- [14 轮 SUMMARY](../14-PyPI分发/SUMMARY.md)：本轮的前置条件 —— src layout 稳定、bundle 期代码已删，是写测试套的合适时机
- `BACKLOG.md` 的"代码质量 / 测试套 `tests/`"条目：本轮兑现的就是这条
