# 14 轮总结 — PyPI 标准分发

## 开发背景

**反思 7-11 轮的方向性错误**。13 轮把项目重构成 src layout + 真 package 之后，我（用户）回头看前面几轮的开发重心，意识到一个方向性问题：

> 在项目还很不正规的阶段（目录结构混乱、推理引擎笨重、没有测试套、分发脚本 patchwork），我花了大量精力去追求"小白用户的一键安装体验" —— 手搓 `.app` bundle、手搓 `.deb`、塞 `python-build-standalone`、写 setup_window 引导首启下载、做 DMG 分发……这是过度设计。

正确的顺序应该是：

1. 把代码质量搞好（src layout、真 package、干净的资源定位）—— 13 轮已做
2. 把架构数据做好（依赖树干净、STT 后端可插拔）—— 10/12 轮已做
3. **分发用最标准的方式：PyPI**。让懂命令行的用户先用上工具，反馈驱动后续迭代
4. 等项目框架稳定了再回头优化小白体验，那时候基础够硬，包装层才值得投入

本轮目标：把 whisper-input 发布到 PyPI，用户通过 `uv tool install whisper-input` / `pipx install whisper-input` 装上就能跑；同时删掉 13 轮留下的所有 bundle 期死代码。

## 实现方案

### 关键设计

#### 1. 决策过程：modelscope 库的体积恐惧被实测纠正

Plan 阶段一度打算保留自研 `stt/downloader.py`。原因是我（Agent）脑子里有一个旧印象：`modelscope` pip 包会把 `pandas` / `scipy` / `pillow` / `datasets` / `oss2` / `addict` 全拉进来，总计"几百 MB"，对 PyPI tool 的"装一下就能用"体验是负收益。

**用户自己在 `/tmp/demo` 里做了实测**，发现 `modelscope 1.35.4` 的 base install 只 36 MB，transitive deps 只有 `certifi / charset_normalizer / filelock / idna / packaging / requests / setuptools / tqdm / urllib3` 这几个轻量库。翻 `modelscope-1.35.4.dist-info/METADATA` 确认：

- **无 extras** → 只有 hub / 下载能力，`from modelscope import snapshot_download` 可用
- `[framework]` extras → 才拉 transformers / scipy / pillow / datasets 等推理栈
- `[cv]` / `[nlp]` / `[audio]` extras → 各自模态的 pipeline 栈
- **torch 不在任何 extras 里** —— 由用户自备

所以 `uv add modelscope`（无 extras）就拿到 36 MB 的干净下载器。`modelscope.pipelines.pipeline()` 因为 `transformers` 没装天然不可达，不会被误用。

这次纠正让我把计划从"保留自研 200 行下载器"改回"用 modelscope 官方库的 snapshot_download"，删掉自研下载器、删掉 SHA256 锁文件。教训是：**体积估算不要凭旧印象，实测比脑补快**。

#### 2. `snapshot_download` 的双仓库调用

SenseVoice 模型分布在两个 ModelScope 仓库：

- `iic/SenseVoiceSmall-onnx` — 主仓库，4 个 ONNX 文件（`model_quant.onnx` / `tokens.json` / `am.mvn` / `config.yaml`），~231 MB
- `iic/SenseVoiceSmall` — 姐妹 PyTorch 仓库，~900 MB 的权重，但我们只需要 `chn_jpn_yue_eng_ko_spectok.bpe.model` 这一个 BPE tokenizer 文件（368 KB）

[src/whisper_input/stt/sense_voice.py](src/whisper_input/stt/sense_voice.py) 的 `load()` 里两次调用：

```python
from modelscope import snapshot_download

onnx_dir = Path(snapshot_download("iic/SenseVoiceSmall-onnx"))
bpe_dir = Path(snapshot_download(
    "iic/SenseVoiceSmall",
    allow_patterns=["chn_jpn_yue_eng_ko_spectok.bpe.model"],
))
```

`allow_patterns` 是关键，没有它第二次调用会拉整个姐妹仓库（~900 MB PyTorch 权重）。同时注意 `snapshot_download` 返回的是 `str` 不是 `pathlib.Path`，外面包 `Path(...)`。

Cache 位置从自研的 `~/Library/Application Support/Whisper Input/models/` 迁到 modelscope 库默认的 `~/.cache/modelscope/hub/`。这是刻意的行为切换：对齐社区标准，未来如果用户同时装别的基于 SenseVoice 的工具可以复用 cache。

#### 3. 6 Phase commit 拆分

按开发宪法"分阶段 commit，每 phase 独立可回滚"原则，拆成 6 个纯净 commit：

| Phase | Commit | 内容 | 净行数变化 |
|---|---|---|---|
| docs | `3a3ff34` | PROMPT.md + PLAN.md 两件套 | +513 |
| 1 | `1597c36` | feat(stt): 模型下载切到 modelscope | +155 / −25 |
| 2 | `51d0cc1` | refactor(stt): 删自研 downloader + model_paths | −349 |
| 3 | `9058cc8` | refactor: 清理 bundle 期全部死代码 + autostart 瘦身 | ~ −2000 |
| 4 | `0200077` | chore(release): 0.4.0 → 0.5.0 + pyproject metadata | +36 / −3 |
| 5 | `4af88a2` | ci: build.yml 精简 + release.yml 新增 | +56 / −135 |
| 6 | `60a43d1` | docs: README / CLAUDE.md / config.example 同步 | +111 / −98 |

特别是 Phase 1 → Phase 2 的拆分不是必需的（完全可以合并成一个 commit），刻意拆开是为了让"切换下载路径"和"物理删除死代码"分开，万一 modelscope 的调用有问题可以单独 revert Phase 1 而保留 Phase 2 的删除意图。

#### 4. GitHub Actions tag 触发 + Trusted Publishing

新增 [.github/workflows/release.yml](.github/workflows/release.yml)：

```yaml
on:
  push:
    tags: ["v*"]
jobs:
  build-and-publish:
    environment: pypi
    permissions:
      id-token: write
      contents: write
    steps:
      - checkout
      - setup-uv
      - verify tag == pyproject version
      - uv build
      - pypa/gh-action-pypi-publish@release/v1   # OIDC, 无 API token
      - softprops/action-gh-release@v2           # 打 GitHub Release
```

这套模式是 httpx / uv / ruff / hatch 等主流 Python 项目的标准做法，PyPA 官方推荐的路径。优势：

- **无 API token** — 通过 OIDC 向 PyPI 证明身份，token rotation 问题彻底消失
- **动作显式** — `git tag v0.5.0 && git push --tags` 是明确的 "发版" 语义，对比"master push 看版本 diff"更少歧义
- **rollback 简单** — 只要删 tag，同时 PyPI 可以 yank

[.github/workflows/build.yml](.github/workflows/build.yml) 精简为只剩 `lint` 一个 job（原来的 `version-check` / `build-macos` / `build-linux` / `release` 四个 job 全删）。保留文件名不变，README badge 不用改。

#### 5. autostart `_bundle_trampoline()` 彻底删除

[src/whisper_input/backends/autostart_macos.py](src/whisper_input/backends/autostart_macos.py) 原来的 `_bundle_trampoline()` 函数检测 `/Contents/Resources/app/` 是否在 `__file__` 路径里——这在 14 轮以前用来判断"当前是不是在 .app bundle 里跑"。14 轮不再有 .app bundle，这个函数的返回值永远是 `None`，是纯死代码。

简化后的 `_program_arguments()`：

```python
def _program_arguments() -> list[str]:
    venv_script = os.path.join(sys.prefix, "bin", "whisper-input")
    if os.path.isfile(venv_script):
        return [venv_script]
    return [sys.executable, "-m", "whisper_input"]
```

这套逻辑对 venv / uv tool / pipx 三种环境都 work —— `sys.prefix/bin/whisper-input` 永远指向当前 Python 环境的 console script。

Linux 那边 [autostart_linux.py](src/whisper_input/backends/autostart_linux.py) 类似，删掉 `/usr/share/applications/whisper-input.desktop`（DEB 安装的）分支，统一从 `whisper_input.assets` package data 读模板。

### 开发内容概括

代码层：

- 删：`packaging/` 整目录（~10 文件，~1300 行）+ `scripts/build.sh`（~330 行）+ `scripts/run_macos.sh` + `stt/downloader.py`（~180 行）+ `stt/model_paths.py`（~170 行）。**总删除 ~2000 行**
- 改：`pyproject.toml`（version + deps + metadata）+ `stt/sense_voice.py`（modelscope 切换）+ `stt/__init__.py`（docstring）+ `backends/autostart_{macos,linux}.py`（瘦身）+ `assets/whisper-input.desktop`（Exec 路径）+ `config_manager.py`（stale 注释）+ `version.py`（docstring）
- 新增：`.github/workflows/release.yml`（~55 行）

文档层：

- 删：README 的 "下载安装包" / "DEB 安装包" / "快速开始"（macOS/Linux 分支）/ 原 "发版流程" 章节
- 改：README "系统要求" + "技术架构"；CLAUDE.md 的 Project Overview / Commands / Architecture / Key Technical Decisions / Dependencies / Upgrading
- 新增：README "安装"（macOS / Linux / 源码三条路径）+ "发版流程（维护者）"；CLAUDE.md "Distribution & Release" 整节

### 额外产物

- **PyPI 包名占位确认**：`https://pypi.org/pypi/whisper-input/json` 返回 404，`whisper-input` 名字在 PyPI 上空着，不需要 fallback 名
- **本地 wheel 端到端验证**：
  - `uv build` 在干净 checkout 上跑通，产出 `dist/whisper_input-0.5.0-py3-none-any.whl`（134 KB，pure-python）和 `dist/whisper_input-0.5.0.tar.gz`
  - `unzip -p METADATA` 检查：authors / license / classifiers / project-url / requires-dist 全部正确渲染
  - `uv tool install --from ./dist/whisper_input-0.5.0-py3-none-any.whl whisper-input` 成功装到 `~/.local/share/uv/tools/whisper-input/`（190 MB 完整 runtime stack）
  - `/Users/jing/.local/bin/whisper-input --help` 正常输出，`--help` 路径因为延迟 import 没有支付 numpy/onnxruntime/modelscope 启动成本
  - 手动 `python -c "from modelscope import snapshot_download; from whisper_input.stt.sense_voice import SenseVoiceSTT"` 确认 tool venv 的 runtime stack 完整
- **TOML 表顺序踩坑**：第一次写 `[project]` 时把 `[project.urls]` 插在 `requires-python` 后面、`dependencies` 前面，hatchling `build_sdist` 报 `URL dependencies of field project.urls must be a string`。原因是 TOML 按顺序把 `dependencies = [...]` 归到 `[project.urls]` 下。修法：`[project.urls]` 必须放在 `dependencies` 和 `[project.scripts]` 之后

## 局限性

### 1. 还没真正发到 pypi.org

本轮只完成了代码侧和 CI workflow 侧的准备。实际发布流程需要用户手动做一次性配置：

1. 在 [pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/) 添加 pending publisher（owner=`pkulijing`, repo=`whisper-input`, workflow=`release.yml`, environment=`pypi`）
2. 在 GitHub repo `Settings → Environments` 下创建 `pypi` 环境
3. `git tag v0.5.0 && git push --tags` 触发 release workflow

这三步属于"后续要做的事"，不在本轮代码 commit 范围内。也可以先走 TestPyPI 过一遍 dry run，确认链路没问题再发正式 PyPI。

### 2. macOS TCC 权限归属的 UX 问题

**PyPI 路线下的固有缺陷**：`uv tool install` / `pipx install` 出来的 `whisper-input` 实际进程是 `~/.local/share/uv/tools/whisper-input/bin/python`（或 pipx 对应路径）。macOS 系统权限对话框弹出的标题是 "python" / "python3"，不是 "Whisper Input"，图标是默认的 Python 图标。用户需要手动去"系统设置 → 隐私与安全性 → 辅助功能 / 输入监听"把这个 Python 二进制加到白名单里。

README 已经讲清楚，但体验上确实不如 `.app` bundle 优雅。`Info.plist` 里我们以前写过的中文麦克风权限描述 `NSMicrophoneUsageDescription` 也失效了，用户看到的是默认英文文案 "python wants to access the microphone"。

**这是 14 轮刻意接受的 trade-off** —— 换来的是"装即可用"的标准 PyPI 路径。未来如果做 thin `.app` wrapper（类似 Karabiner 的做法，bundle 只做权限注册、内部还是跑 `uv tool` 出来的 Python），可以把权限 UX 找回来。

### 3. 首次模型下载没有进度 UI

以前自研 `stt/downloader.py` 有 `ProgressCallback`，`setup_window.py` 根据回调更新 tkinter 进度条。换成 `modelscope.snapshot_download` 之后，库用 tqdm 打到 stdout。在托盘模式下（`whisper-input --no-tray` 以外的默认行为），用户看不到下载进度，会感觉"卡住"。

**临时缓解**：首次下载的 tqdm 输出会写到托盘启动日志（macOS: `~/Library/Logs/WhisperInput.log`，Linux: stderr），用户可以 `tail -f` 看进度。但这不是友好 UX。

**未来方向**：可以 hook modelscope 的 tqdm，把字节数转成事件推到 Web UI 上显示一个原生进度条。属于小白体验优化，推到 15 轮以后。

### 4. 没在 Linux 实机验证

本轮开发环境是 macOS。`uv build` 产出的 wheel 是 `py3-none-any.whl`（pure python），理论上 Linux 装应该一样能 work，平台差异完全靠 `pyproject.toml` 的 `sys_platform` marker 分流（`evdev` 仅 Linux、`pynput` 仅 macOS）。但没实机跑过。

**风险点**：
- Linux `libgirepository-2.0-dev` 的系统依赖文档正确性（README 已写，但没在干净 Ubuntu 24.04 VM 上验证）
- `xdotool` / `xclip` / `libportaudio2` 的完整性

### 5. 模型 cache 位置迁移会触发"重新下载"

用户如果在 13 轮或更早版本跑过 whisper-input，模型会在 `~/Library/Application Support/Whisper Input/models/iic-SenseVoiceSmall-onnx/`（自研 cache）。14 轮切到 modelscope 默认的 `~/.cache/modelscope/hub/iic/SenseVoiceSmall-onnx/`，是**不同的目录**。

首次运行 0.5.0 会重新从 ModelScope 拉 231 MB。老 cache 不会被自动清理（安全起见），留给用户手动 `rm -rf`。

**不做自动迁移**的理由是：迁移代码属于"一次性兼容层"，在项目这个阶段写了只会让 sense_voice.py 变重，且用户绝大多数都是新装，不值得。

### 6. `uv.lock` 里 `pygobject` 的平台差异

`uv sync` 在 macOS 上跑时不装 `pygobject`（`sys_platform == 'linux'` marker 过滤）。lock 文件里 pygobject 的记录仍然存在，但不参与 install。这是 uv 的预期行为，非本轮引入的。发 PyPI wheel 时 hatchling 不 bake lock 文件，所以下游用户 `pip install whisper-input` 时重新解析依赖，Linux 用户会拿到 pygobject，macOS 用户不会。已通过 `unzip -l` wheel 确认 lock 文件不在 wheel 里。

## 后续 TODO

**优先级高（15 轮候选）**：

1. **真正发到 pypi.org**：完成一次性 Trusted Publisher 配置 + 打 `v0.5.0` tag，确认 release workflow 跑绿、PyPI 页面出现包、`pip install whisper-input==0.5.0` 能装。可以先走 TestPyPI 过 dry run。**这是本轮真正"收工"的动作**
2. **Linux 实机验证**：在干净 Ubuntu 24.04 VM 上 `uv tool install whisper-input`，走完完整链路，记录系统依赖是否漏写
3. **首次下载的进度 UI**：hook modelscope 的 tqdm 或自己调 tqdm.write，把字节数事件推到 Web 设置页或托盘 tooltip 上

**优先级中**：

4. **macOS thin `.app` wrapper**：bundle 只做权限注册和图标，内部调 `~/.local/bin/whisper-input`。解决 TCC 权限归属 + `NSMicrophoneUsageDescription` 文案 + Dock 图标。类似 Karabiner 的做法。属于小白体验优化
5. **测试套 `tests/`**：从头加 pytest，目标是 `stt/sense_voice.py` 的解码路径 + `config_manager.py` 的 YAML 处理 + `backends/autostart_*.py` 的 plist / desktop 写入
6. **`--install-autostart` / `--uninstall-autostart` CLI 子命令**：14 轮决定不做，但如果后续发现"设置菜单开关"对 headless 用户不友好，可以加命令式开关

**优先级低**：

7. **模型版本追新**：当 DAMO 在 ModelScope 推新 revision 时，给 `snapshot_download(..., revision="<tag>")` 加个参数。本轮 revision 省略了，默认拿 master（也就是最新），升级时手动测一下推理没回归就行

### 本轮内已清理的 TODO

- ✅ `config.example.yaml` 的 stale 注释（引用已删的 `stt/model_paths.py`）已改为 modelscope 描述
- ✅ `config_manager.py` 的 stale 注释（"GitHub release + ghproxy + 160MB"）已改为 modelscope 231MB
- ✅ `version.py` 和 `.gitignore` 里关于 `build.sh` 的注释已改为中性描述

## 相关文档

- [PROMPT.md](PROMPT.md) — 需求文档
- [PLAN.md](PLAN.md) — 实施计划（6 phase 分解 + 关键事实 + 验证计划）
- 前置重构：[13 轮 SUMMARY](../13-目录重构/SUMMARY.md) 把项目变成真 package（src layout + hatchling editable build + `importlib.resources` 资源定位），是本轮 PyPI 化的必要前提。没有 13 轮这套改造，wheel 构建不起来
- memory [project_distribution_pivot.md](/Users/jing/.claude/projects/-Users-jing-Developer-whisper-input/memory/project_distribution_pivot.md) 记录了转向决策的 why
